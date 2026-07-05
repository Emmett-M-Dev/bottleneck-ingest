"""Export the Foyle Placement Tracker bottlenecks as UI cases.

Same output contract as bridge/export_foyle.py (ui_cases.json + ui_workflow.json),
so the dashboard consumes it unchanged. The model underneath is the single-sheet
tracker: detection runs over the event log derived from one cohort-per-row sheet
(detect_foyle_tracker), and the workflow map uses the tracker's task order.
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
from detection.detect import detect_foyle_tracker, load_event_log

_TEMPLATES = {
    "delay": {
        "title": "Accommodation and placements locked days before arrival",
        "workflow_area": "Placement & accommodation",
        "severity": "high",
        "confidence": 0.85,
        "description": (
            "Host accommodation and work placements are being confirmed on average "
            "{metric:.0f} days before the cohort lands — some on the eve of arrival. "
            "The scramble recurs across {count} cohorts, leaving no slack if a host or "
            "company falls through."
        ),
        "fix": {
            "summary": "Lock accommodation and placements against a fixed lead-time SLA",
            "steps": [
                "Set a target of confirming accommodation and placement at least three weeks before arrival.",
                "Flag any cohort whose accommodation or placement is still unconfirmed inside that window.",
                "Hold a small standby pool of hosts so a late drop-out does not push confirmation to the wire.",
            ],
            "rationale": (
                "Late confirmation leaves no recovery time. Working to a stated lead-time and "
                "watching the window turns a last-minute scramble into a routine checkpoint."
            ),
        },
    },
    "outstanding": {
        "title": "Critical prep tasks still open as arrival approaches",
        "workflow_area": "Pre-arrival checklist",
        "severity": "high",
        "confidence": 0.9,
        "description": (
            "Critical pre-arrival tasks — enrolment, documents, placement or accommodation "
            "— are still marked Not Started or In Progress for {count} cohorts. Each open "
            "task is a student who could arrive without a confirmed plan."
        ),
        "fix": {
            "summary": "Drive every critical task to Complete before arrival on one tracker",
            "steps": [
                "Keep the placement tracker as the single source of truth, one row per cohort.",
                "Review any cohort with a critical task still open inside the arrival window.",
                "Make each critical task owner and due-date explicit so nothing sits untouched.",
            ],
            "rationale": (
                "Tasks slip because status lives in scattered sheets and inboxes. One dropdown-"
                "locked tracker makes an open critical task impossible to miss."
            ),
        },
    },
    "deposit": {
        "title": "Self-catering deposits unpaid before arrival",
        "workflow_area": "Invoicing & payments",
        "severity": "medium",
        "confidence": 0.85,
        "description": (
            "Self-catering deposits are still outstanding at arrival for {count} cohorts, "
            "tying up cash Foyle has already committed to accommodation and forcing staff to "
            "chase payment by hand."
        ),
        "fix": {
            "summary": "Collect the self-catering deposit on confirmation, chase on an SLA",
            "steps": [
                "Request the deposit the moment self-catering accommodation is confirmed.",
                "State a clear payment deadline to the partner and remind before it lapses.",
                "Flag any deposit still unpaid inside the arrival window for escalation.",
            ],
            "rationale": (
                "The deposit secures accommodation Foyle has already paid for. Collecting on "
                "confirmation and chasing against an SLA protects cash without adding headcount."
            ),
        },
    },
}


def build_cases() -> list[dict]:
    texts = _load_record_texts()
    detected = detect_foyle_tracker()
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
    detected = detect_foyle_tracker(df)

    stage_to_bn = {bn.stage: bn for bn in detected if bn.affected_count > 0}
    canon_to_label = {s.lower(): s for s in config.FOYLE_TRACKER_STAGE_ORDER}

    present = {s for s in df["stage"].unique() if s in canon_to_label}
    nodes = []
    for label in config.FOYLE_TRACKER_STAGE_ORDER:
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
    print(f"Exported {n} Foyle tracker UI cases to {config.UI_CASES_PATH.relative_to(config.ROOT)}")
    print(f"Exported workflow ({len(workflow['nodes'])} stages, "
          f"{len(workflow['edges'])} transitions) to {config.UI_WORKFLOW_PATH.relative_to(config.ROOT)}")
    print(f"KPIs: {workflow['kpis']}")


if __name__ == "__main__":
    main()
