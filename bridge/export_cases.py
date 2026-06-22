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
from detection.detect import DetectedBottleneck, detect_all, load_event_log
from pipeline.embed import query

# ── Authored content per bottleneck type ─────────────────────────────────────
# Detection finds the pattern and the numbers; this supplies the human framing and
# the suggested fix. Keyed by detector `type`.
_TEMPLATES = {
    "delay": {
        "title": "Placement confirmations delayed by one to two weeks",
        "workflow_area": "Placement confirmations",
        "severity": "high",
        "confidence": 0.84,
        "description": (
            "Booking Confirmed is taking far longer than it should — around "
            "{metric:.0f} days from the previous step, against an expected 1-2 days. "
            "The delay recurs across {count} of the placement cases and is the slowest "
            "step in the workflow, with host-family matching the usual hold-up."
        ),
        "fix": {
            "summary": "Acknowledge on request and put an SLA on host-family confirmation",
            "steps": [
                "Send an automatic 'request received' acknowledgement the moment a placement request arrives.",
                "Set an explicit confirmation SLA (e.g. host family matched within 3 working days) and state it to the partner school.",
                "Flag any placement still unconfirmed after 48 hours for priority host-matching.",
            ],
            "rationale": (
                "The gap sits entirely in the confirmation step while a host family is found. An "
                "immediate acknowledgement removes the partner school's uncertainty, and an SLA "
                "with an aging flag stops individual placements from quietly slipping past a week."
            ),
        },
    },
    "repetition": {
        "title": "Student documents re-requested and re-keyed across sheets",
        "workflow_area": "Student documents",
        "severity": "medium",
        "confidence": 0.95,
        "description": (
            "Student paperwork (CV, motivation letter, parental consent) is collected a "
            "second time and re-keyed into the homestay and enrolment sheets after the "
            "Invoice Issued step — a duplicate-entry stage that appears in {count} cases. "
            "The double handling is wasted effort and a transcription-error risk."
        ),
        "fix": {
            "summary": "Hold student documents once in a single record instead of re-collecting",
            "steps": [
                "Populate the homestay and enrolment sheets from the student's document record automatically.",
                "Remove the manual Document Re-request step from the process.",
                "Spot-check the first week of auto-carried rows against source before switching off manual entry.",
            ],
            "rationale": (
                "The re-request stage exists only because the document record and the placement "
                "sheets are not linked. Carrying the student's details forward from a single record "
                "removes the second collection point entirely and the errors that come with it."
            ),
        },
    },
    "rework": {
        "title": "Host placements re-allocated (looped back) before confirmation",
        "workflow_area": "Host-family placement",
        "severity": "medium",
        "confidence": 0.90,
        "description": (
            "Placements are being re-allocated after they were first offered — a rework "
            "loop that appears in {count} cases and pushes the booking back. Re-allocation "
            "this early usually means a host family dropped out or was matched on incomplete "
            "student requirements."
        ),
        "fix": {
            "summary": "Confirm host availability and student requirements up front to cut the re-allocation loop",
            "steps": [
                "Add a short pre-placement checklist so host availability and student needs are confirmed before the first offer.",
                "Re-confirm each host family's commitment before the placement is offered to the school.",
                "Track re-allocation reasons so the most common drop-out causes can be designed out.",
            ],
            "rationale": (
                "The loop-back is driven by host drop-outs and incomplete initial matches. Confirming "
                "availability and requirements before the first offer removes the most common reason "
                "for re-allocating a placement."
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


def build_workflow() -> dict:
    """Workflow graph + headline KPIs for the dashboard, built from the real event
    log. Nodes are the canonical stages (in order); edges are observed stage->stage
    transitions with counts; bottleneck stages are flagged with their detected id."""
    df = load_event_log()
    detected = detect_all(df)

    # Map a canonical stage label -> detected bottleneck (if any).
    stage_to_bn = {bn.stage: bn for bn in detected if bn.affected_count > 0}
    canon_to_label = {s.lower(): s for s in config.STAGE_ORDER}

    present = {s for s in df["stage"].unique() if s in canon_to_label}
    nodes = []
    for label in config.STAGE_ORDER:
        if label.lower() not in present:
            continue
        bn = stage_to_bn.get(label)
        nodes.append({
            "id": label,
            "label": label,
            "bottleneck": bn.id if bn else None,
            "bottleneck_type": bn.type if bn else None,
            "affected": bn.affected_count if bn else 0,
        })

    # Observed transitions between consecutive events within each case.
    transitions: dict[tuple[str, str], int] = {}
    for _, g in df.sort_values("ts").groupby("case_id"):
        seq = [canon_to_label[s] for s in g["stage"] if s in canon_to_label]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                transitions[(a, b)] = transitions.get((a, b), 0) + 1
    edges = [{"from": a, "to": b, "count": c} for (a, b), c in sorted(transitions.items())]

    affected_cases = {c for bn in detected for c in bn.affected_cases}
    kpis = {
        "cases": int(df["case_id"].nunique()),
        "events": int(len(df)),
        "open_bottlenecks": sum(1 for bn in detected if bn.affected_count > 0),
        "cases_affected": len(affected_cases),
    }
    return {"nodes": nodes, "edges": edges, "kpis": kpis}


def export(cases_path=None, workflow_path=None) -> tuple[int, dict]:
    cases_path = cases_path or config.UI_CASES_PATH
    workflow_path = workflow_path or config.UI_WORKFLOW_PATH

    cases = build_cases()
    workflow = build_workflow()

    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(cases), workflow


def main() -> None:
    n, workflow = export()
    rel_c = config.UI_CASES_PATH.relative_to(config.ROOT)
    rel_w = config.UI_WORKFLOW_PATH.relative_to(config.ROOT)
    print(f"Exported {n} UI cases to {rel_c}")
    print(f"Exported workflow ({len(workflow['nodes'])} stages, "
          f"{len(workflow['edges'])} transitions) to {rel_w}")
    print(f"KPIs: {workflow['kpis']}")


if __name__ == "__main__":
    main()
