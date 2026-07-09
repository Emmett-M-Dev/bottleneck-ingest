"""RAG diagnosis agent — the LLM reasoning step of the fixed pipeline core.

Per detected bottleneck: retrieve the nearest past resolutions from the
"sme_resolutions" ChromaDB collection, build a scrubbed evidence payload, and
ask Claude for a structured diagnosis + fix suggestion. Output is
schema-constrained via `messages.parse` -> `DiagnosisResult`, so a
syntactically invalid diagnosis cannot come back.

Mirrors audit/infer.py: no `temperature` (sampling parameters are removed on
Opus 4.8 and return a 400) — adaptive thinking is used instead; the client is
injectable so tests never hit the network; and zero raw PII reaches the API —
every evidence excerpt passes through scrub.anonymise.scrub_text before it
enters the payload, and the corpus text is PII-free by construction.

IMPORT CONSTRAINT: this module must never import chromadb/pyarrow/torch at
module scope (the Windows native-lib segfault documented in ingest.py). The
retrieval helper imports pipeline.embed_resolutions lazily, inside the
function, so `import pipeline.diagnose` stays light.

Model: `claude-opus-4-8` by default; override with DIAGNOSE_MODEL. Auth via
ANTHROPIC_API_KEY (see load_dotenv()).
"""

from __future__ import annotations

import json
import os

import anthropic
from pydantic import BaseModel, Field

import config
from scrub.anonymise import scrub_text

DEFAULT_MODEL = os.environ.get("DIAGNOSE_MODEL", config.DIAGNOSE_MODEL_DEFAULT)

SYSTEM_PROMPT = """\
You are an operations analyst diagnosing workflow bottlenecks for a small
business.

You receive a JSON payload with:
- bottleneck: one detected bottleneck — its type (delay = cases wait too long
  entering the stage; repetition = a duplicate-work stage appears; rework = a
  loop-back stage appears), the workflow stage, its metric, how many cases are
  affected, and anonymised evidence excerpts ([PERSON_n]/[MASKED_n] etc. are
  scrubbed placeholders).
- sme_context: the business domain and its workflow stage order.
- past_resolutions: how similar problems were fixed before, retrieved from the
  organisation's resolution log. Some may not be relevant — judge each one.

Return:
- diagnosis: what is happening, grounded ONLY in the supplied evidence and
  metrics. Plain language, no placeholder tokens.
- root_cause: the most plausible underlying cause, stated as one clear claim.
- suggested_fix: a fix the staff could adopt — a one-line summary, three to
  five concrete steps, and a rationale. Prefer actions that worked in the
  relevant past resolutions, adapted to this bottleneck; say so in the
  rationale when you do.
- confidence: 0-1. Be conservative — thin evidence or weakly relevant past
  resolutions deserve lower confidence.
- retrieved_resolution_ids: the ids of ONLY the past resolutions that actually
  informed the fix (a subset of those supplied; possibly empty).
"""


class SuggestedFix(BaseModel):
    summary: str
    steps: list[str]
    rationale: str


class DiagnosisResult(BaseModel):
    diagnosis: str
    root_cause: str
    suggested_fix: SuggestedFix
    confidence: float = Field(ge=0.0, le=1.0)
    retrieved_resolution_ids: list[str]


def load_dotenv() -> None:
    """Load KEY=VALUE lines from the repo's .env (gitignored) into os.environ.
    Same minimal helper as audit/run.py — no quoting/expansion, no dependency."""
    env_file = config.ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def retrieve_resolutions(bn_text: str, profile: str, n: int = 3,
                         all_profiles: bool = False) -> list[dict]:
    """Top-n past resolutions for a bottleneck description (the RAG step).

    Lazy-imports the embedding stack so importing this module never loads
    chromadb/torch. Distances (normalised-embedding L2) map to a 0..1
    similarity the same way bridge/export_cases does."""
    from pipeline import embed_resolutions  # noqa: PLC0415 — heavy libs load here only

    res = embed_resolutions.query_resolutions(
        bn_text, n_results=n, profile=None if all_profiles else profile)
    docs = (res.get("documents") or [[]])[0]
    ids = (res.get("ids") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    out: list[dict] = []
    for i, doc in enumerate(docs):
        dist = dists[i] if i < len(dists) else 1.0
        meta = metas[i] if i < len(metas) else {}
        out.append({
            "resolution_id": ids[i] if i < len(ids) else f"res-{i}",
            "source": f"resolutions:{meta.get('source', 'corpus')}",
            "similarity_score": round(max(0.0, min(1.0, 1.0 - dist / 2.0)), 2),
            "summary": (doc or "").strip()[:200],
            "document": (doc or "").strip(),
            "stage": meta.get("stage"),
            "bottleneck_type": meta.get("bottleneck_type"),
            "days_to_resolve": meta.get("days_to_resolve"),
        })
    return out


def _build_payload(bn: dict, profile: str, retrieved: list[dict]) -> dict:
    """The exact JSON the model sees. Every free-text evidence excerpt passes
    through scrub_text here — the zero-PII guarantee for this agent (the
    corpus text and SME context are PII-free by construction; no brand names)."""
    ui = config.MESSY_PROFILES[profile].get("ui", {})
    return {
        "bottleneck": {
            "id": bn.get("id"),
            "type": bn.get("type"),
            "stage": bn.get("stage"),
            "metric_label": bn.get("metric_label"),
            "metric_value": bn.get("metric_value"),
            "affected_count": bn.get("affected_count",
                                     len(bn.get("affected_cases", []))),
            "evidence_excerpts": [scrub_text(str(e))[0]
                                  for e in bn.get("evidence_excerpts", [])],
        },
        "sme_context": {
            "domain": ui.get("domain", "small business operations"),
            "workflow_stages": config.MESSY_PROFILES[profile]["stage_order"],
        },
        "past_resolutions": [
            {
                "resolution_id": r["resolution_id"],
                "resolution": r.get("document") or r.get("summary", ""),
                "days_to_resolve": r.get("days_to_resolve"),
            }
            for r in retrieved
        ],
    }


def diagnose(bn: dict, profile: str, *, retrieved: list[dict] | None = None,
             model: str | None = None,
             client: anthropic.Anthropic | None = None) -> DiagnosisResult:
    """One structured-output call per bottleneck. `client` is injectable so
    tests stub it; `retrieved` is injectable so callers (and tests) can skip
    the chromadb retrieval entirely."""
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the diagnosis agent needs it to "
            "call Claude. Set it, or run with --offline for the template/"
            "heuristic fallback."
        )
    if retrieved is None:
        retrieved = retrieve_resolutions(
            f"{bn.get('stage', '')} {bn.get('type', '')} bottleneck", profile)
    client = client or anthropic.Anthropic()
    model = model or DEFAULT_MODEL
    payload = _build_payload(bn, profile, retrieved)
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False)}],
            output_format=DiagnosisResult,
        )
    except anthropic.AuthenticationError as exc:
        raise RuntimeError(
            "Anthropic API auth failed — set ANTHROPIC_API_KEY, or run with "
            "--offline for the template/heuristic fallback."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError(
            "Could not reach the Anthropic API — check the network, or run "
            "with --offline for the template/heuristic fallback."
        ) from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(
            f"Anthropic API error {exc.status_code} on model {model}: {exc.message}"
        ) from exc
    return response.parsed_output


def offline_diagnosis(bn: dict, retrieved: list[dict] | None = None) -> DiagnosisResult:
    """Deterministic no-LLM fallback: restate the detection and lean on the
    top retrieved resolution for the fix. Used by `pipeline.agent --offline`
    and as the failure fallback in the diagnose node."""
    retrieved = retrieved or []
    stage = bn.get("stage", "unknown stage")
    btype = bn.get("type", "bottleneck")
    count = bn.get("affected_count", len(bn.get("affected_cases", [])))
    metric = bn.get("metric_label", "metric"), bn.get("metric_value", 0)
    top = retrieved[0] if retrieved else None
    steps = [
        f"Review the '{stage}' step with the staff who work it.",
        "Compare the affected cases against the unaffected ones for what differs.",
    ]
    if top:
        steps.append(f"Consider the approach from {top['resolution_id']}: "
                     f"{top.get('summary', '')[:120]}")
    return DiagnosisResult(
        diagnosis=(f"{count} cases show the '{stage}' {btype} pattern "
                   f"({metric[0]}={metric[1]:g})."),
        root_cause=("Not determined — offline heuristic; a human should review "
                    "the evidence."),
        suggested_fix=SuggestedFix(
            summary=f"Review the '{stage}' step against past resolutions",
            steps=steps,
            rationale=("Offline fallback: detection numbers plus the nearest "
                       "past resolutions, without LLM reasoning."),
        ),
        confidence=0.5,
        retrieved_resolution_ids=[r["resolution_id"] for r in retrieved],
    )
