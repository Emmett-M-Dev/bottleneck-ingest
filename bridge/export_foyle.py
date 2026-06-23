"""Export the multi-sheet Foyle bottlenecks as UI cases (Phase B).

Same output contract as bridge/export_cases.py (ui_cases.json + ui_workflow.json),
so the dashboard consumes it unchanged. The difference is the model underneath:
detection runs over the event log derived from the six staff sheets + three emails
(detect_foyle), and the workflow map uses the Foyle stage order with Payment
Received as the delay stage.

Evidence, RAG retrieval and record-text loading are reused from export_cases — only
the templates, the detector, and the stage order are Foyle-specific.
"""

from __future__ import annotations

import json

import config
from bridge.export_cases import (
    _evidence,
    _load_record_texts,
    _retrieved_resolutions,
    _utc_now_iso,
)
from detection.detect import detect_foyle, load_event_log

_TEMPLATES = {
    "delay": {
        "title": "Partner invoices paid weeks after they are issued",
        "workflow_area": "Invoicing & payments",
        "severity": "high",
        "confidence": 0.85,
        "description": (
            "Payment is landing around {metric:.0f} days after the invoice is issued, "
            "well past the expected fortnight. The lag recurs across {count} of the "
            "cohort's invoices — some still unpaid at arrival — tying up cash and forcing "
            "staff to chase partner schools by hand."
        ),
        "fix": {
            "summary": "Issue invoices on confirmation and chase on a fixed payment SLA",
            "steps": [
                "Generate the invoice automatically the moment a placement is confirmed, not days later.",
                "State a clear payment SLA to the partner school and send a reminder before it lapses.",
                "Flag any invoice unpaid 21 days after issue, and any still pending at arrival, for escalation.",
            ],
            "rationale": (
                "The gap sits entirely between issue and payment. Invoicing on confirmation and "
                "chasing against a stated SLA shortens the cycle without adding finance headcount."
            ),
        },
    },
    "repetition": {
        "title": "Student documents re-requested and re-keyed across sheets",
        "workflow_area": "Student documents",
        "severity": "medium",
        "confidence": 0.95,
        "description": (
            "The same student paperwork (CV, motivation letter, parental consent) is chased "
            "a second time and re-typed into the placements, host-family and document sheets, "
            "where the same name is even spelled differently. The re-keying appears in {count} "
            "cases and is a transcription-error risk."
        ),
        "fix": {
            "summary": "Hold each student record once and let every sheet read from it",
            "steps": [
                "Keep one student record (name, age, documents) and reference it from the other sheets.",
                "Remove the manual Document Re-request step by carrying documents forward automatically.",
                "Reconcile existing sheets so the re-keyed name variants resolve to one student.",
            ],
            "rationale": (
                "Re-requests exist because the six sheets are not linked, so staff re-collect and "
                "re-type the same data. A single student record removes the duplicate entry and the "
                "name-mismatch errors that come with it."
            ),
        },
    },
    "rework": {
        "title": "Placements re-allocated when hosts drop out or companies decline",
        "workflow_area": "Host-family & work placement",
        "severity": "medium",
        "confidence": 0.90,
        "description": (
            "Placements are being re-allocated after they were first offered — a host family "
            "pulls out, or a work-placement company declines — forcing staff to re-house or "
            "re-place the student. The loop appears in {count} cases and pushes confirmation back."
        ),
        "fix": {
            "summary": "Re-confirm host and company commitment before the placement is offered",
            "steps": [
                "Re-confirm each host family's and company's availability before offering the placement.",
                "Keep a small standby pool of hosts/companies so a drop-out is filled without a full re-search.",
                "Track drop-out and decline reasons so the most common causes can be designed out.",
            ],
            "rationale": (
                "Re-allocation is driven by late host drop-outs and company declines. Confirming "
                "commitment up front and holding standby capacity removes most of the rework."
            ),
        },
    },
}


def build_cases() -> list[dict]:
    texts = _load_record_texts()
    detected = detect_foyle()
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
            "description": tpl["description"].format(metric=bn.metric_value, count=bn.affected_count),
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
    df = load_event_log()
    detected = detect_foyle(df)

    stage_to_bn = {bn.stage: bn for bn in detected if bn.affected_count > 0}
    canon_to_label = {s.lower(): s for s in config.FOYLE_STAGE_ORDER}

    present = {s for s in df["stage"].unique() if s in canon_to_label}
    nodes = []
    for label in config.FOYLE_STAGE_ORDER:
        if label.lower() not in present:
            continue
        bn = stage_to_bn.get(label)
        nodes.append({
            "id": label, "label": label,
            "bottleneck": bn.id if bn else None,
            "bottleneck_type": bn.type if bn else None,
            "affected": bn.affected_count if bn else 0,
        })

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
    print(f"Exported {n} Foyle UI cases to {config.UI_CASES_PATH.relative_to(config.ROOT)}")
    print(f"Exported workflow ({len(workflow['nodes'])} stages, "
          f"{len(workflow['edges'])} transitions) to {config.UI_WORKFLOW_PATH.relative_to(config.ROOT)}")
    print(f"KPIs: {workflow['kpis']}")


if __name__ == "__main__":
    main()
