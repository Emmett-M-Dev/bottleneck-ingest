"""Export detected bottlenecks as UI-ready cases for the HITL dashboard.

This is the seam between the two repos. The heavy lifting (spaCy / torch / ChromaDB)
all lives here in `bottleneck-ingest`; the dashboard (`hitl-interface`) is deliberately
forbidden from importing those libraries. So the output crosses the boundary as a plain
JSON file — `outputs/ui_cases.json` — shaped exactly like the dashboard's `BottleneckCase`
schema. The dashboard reads it like it reads any fixture; no heavy code is shared.

Per detected bottleneck this assembles:
  - real metrics + example excerpts from the (already scrubbed) records as evidence,
  - an authored suggested fix for that pattern,
  - retrieved_resolutions: the nearest chunks from the ChromaDB vector store (RAG),
    so the export demonstrates the embeddings doing real retrieval work.

No PII: every excerpt comes from records that were scrubbed at ingest time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import config
from detection.detect import DetectedBottleneck, detect_all
from pipeline.embed import query

# ── Authored content per bottleneck type ─────────────────────────────────────
# Detection finds the pattern and the numbers; this supplies the human framing and
# the suggested fix. Keyed by detector `type`.
_TEMPLATES = {
    "delay": {
        "title": "Booking confirmations delayed by one to two weeks",
        "workflow_area": "Booking confirmations",
        "severity": "high",
        "confidence": 0.84,
        "description": (
            "Booking Confirmation is taking far longer than it should — around "
            "{metric:.0f} days from the previous step, against an expected 1-2 days. "
            "The delay recurs across {count} of the booking cases and is the slowest "
            "step in the workflow."
        ),
        "fix": {
            "summary": "Acknowledge on submission and put an SLA on confirmation",
            "steps": [
                "Send an automatic 'request received' acknowledgement the moment a booking is submitted.",
                "Set an explicit confirmation SLA (e.g. next working day) and state it to the customer.",
                "Flag any booking still unconfirmed after 48 hours for priority handling.",
            ],
            "rationale": (
                "The gap sits entirely in the confirmation step. An immediate acknowledgement "
                "removes customer uncertainty, and an SLA with an aging flag stops individual "
                "bookings from quietly slipping past a week."
            ),
        },
    },
    "repetition": {
        "title": "Payment details re-entered into the pre-arrival sheet",
        "workflow_area": "Payments",
        "severity": "medium",
        "confidence": 0.95,
        "description": (
            "Payment information is keyed a second time into the pre-arrival sheet after "
            "the Payment step — a duplicate-entry stage that appears in {count} cases. "
            "The double entry is wasted effort and a transcription-error risk."
        ),
        "fix": {
            "summary": "Carry payment data forward from a single source instead of re-keying",
            "steps": [
                "Populate the pre-arrival sheet from the payment record automatically.",
                "Remove the manual Payment Re-entry step from the process.",
                "Spot-check the first week of auto-carried rows against source before switching off manual entry.",
            ],
            "rationale": (
                "The re-entry stage exists only because the two sheets are not linked. "
                "Generating the pre-arrival row from the payment record removes the second "
                "entry point entirely and the errors that come with it."
            ),
        },
    },
    "rework": {
        "title": "Quotes revised (looped back) before booking is confirmed",
        "workflow_area": "Sales quotes",
        "severity": "medium",
        "confidence": 0.90,
        "description": (
            "Quotes are being revised after they were first issued — a rework loop that "
            "appears in {count} cases and pushes the booking back. Rework this early "
            "usually means the initial quote was missing information."
        ),
        "fix": {
            "summary": "Capture quote requirements up front to cut the revision loop",
            "steps": [
                "Add a short pre-quote checklist so the key details are captured before the first quote.",
                "Have a second person sanity-check quotes above a value threshold before they go out.",
                "Track revision reasons so the most common gaps can be designed out.",
            ],
            "rationale": (
                "The loop-back is driven by incomplete initial quotes. Capturing the missing "
                "inputs before the first quote removes the most common reason for revising it."
            ),
        },
    },
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_record_texts() -> dict[str, str]:
    """source_ref -> scrubbed record text, for building evidence excerpts."""
    out: dict[str, str] = {}
    if not config.RECORDS_PATH.exists():
        return out
    with config.RECORDS_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            out[rec.get("source_ref", "")] = rec.get("text", "")
    return out


def _ui_source_type(source_ref: str) -> str:
    """Map an internal source to the UI's SourceType literal (excel|email|text).
    Structured rows (sheets/excel) are tabular -> 'excel'; ops notes -> 'text'."""
    return "text" if source_ref.endswith(".txt") or ":txt" in source_ref else "excel"


def _evidence(bn: DetectedBottleneck, texts: dict[str, str]) -> list[dict]:
    items = [{
        "source_type": "excel",
        "excerpt": (
            f"{bn.affected_count} cases show the '{bn.stage}' pattern in the live event log."
        ),
        "metric": f"{bn.metric_label}={bn.metric_value:g}",
        "reference": "event_log.parquet",
    }]
    for ref in bn.example_refs[:2]:
        excerpt = texts.get(ref, "").strip()
        if excerpt:
            items.append({
                "source_type": _ui_source_type(ref),
                "excerpt": excerpt[:240],
                "metric": None,
                "reference": ref,
            })
    return items


def _retrieved_resolutions(bn: DetectedBottleneck, n: int = 3) -> list[dict]:
    """Nearest chunks from ChromaDB for this bottleneck's description — the RAG step.
    Distances (normalised-embedding L2) are mapped to a 0..1 similarity score."""
    res = query(f"{bn.stage} {bn.type} bottleneck", n_results=n)
    docs = (res.get("documents") or [[]])[0]
    ids = (res.get("ids") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    out: list[dict] = []
    for i, doc in enumerate(docs):
        dist = dists[i] if i < len(dists) else 1.0
        similarity = max(0.0, min(1.0, 1.0 - dist / 2.0))
        meta = metas[i] if i < len(metas) else {}
        out.append({
            "resolution_id": ids[i] if i < len(ids) else f"chunk-{i}",
            "source": f"corpus:{meta.get('source_ref', 'sme_ops')}",
            "similarity_score": round(similarity, 2),
            "summary": (doc or "").strip()[:200],
        })
    return out


def build_cases() -> list[dict]:
    texts = _load_record_texts()
    detected = detect_all()
    detected_at = _utc_now_iso()

    cases: list[dict] = []
    for bn in detected:
        tpl = _TEMPLATES[bn.type]
        cases.append({
            "case_id": bn.id,
            "detected_at": detected_at,
            "title": tpl["title"],
            "workflow_area": tpl["workflow_area"],
            "severity": tpl["severity"],
            "confidence": tpl["confidence"],
            "description": tpl["description"].format(
                metric=bn.metric_value, count=bn.affected_count
            ),
            "evidence": _evidence(bn, texts),
            "suggested_fix": {
                "summary": tpl["fix"]["summary"],
                "steps": tpl["fix"]["steps"],
                "rationale": tpl["fix"]["rationale"],
            },
            "retrieved_resolutions": _retrieved_resolutions(bn),
            "status": "pending",
        })
    return cases


def export(path=None) -> int:
    path = path or config.UI_CASES_PATH
    cases = build_cases()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(cases)


def main() -> None:
    n = export()
    print(f"Exported {n} UI cases to {config.UI_CASES_PATH.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
