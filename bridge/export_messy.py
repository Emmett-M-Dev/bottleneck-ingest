"""Export messy-drive bottlenecks as UI cases, per SME profile.

    python -m bridge.export_messy --profile foyle

Same output contract as the other exporters (ui_cases.json + ui_workflow.json)
so the dashboard consumes it unchanged. Detection is the generic
delay/repetition/rework detector parametrised by the profile's marker stages
(config.MESSY_PROFILES) — the whole point: onboarding a second SME adds a
config block and a template dict here, zero new reader or detector code.
"""

from __future__ import annotations

import argparse
import json

import config
from bridge.export_cases import (
    _evidence,
    _load_record_texts,
    _retrieved_resolutions,
    _utc_now_iso,
)
from detection.detect import detect_generic, load_event_log

_TEMPLATES: dict[tuple[str, str], dict] = {
    ("foyle", "delay"): {
        "title": "Bookings stall before confirmation",
        "workflow_area": "Booking pipeline",
        "severity": "high",
        "confidence": 0.85,
        "description": (
            "Bookings sit an average of {metric:.0f} days between the previous "
            "step and Booking Confirmed — {count} bookings crossed the "
            "week-long mark while a host was being found."
        ),
        "fix": {
            "summary": "Work host-matching to a confirmation SLA",
            "steps": [
                "Set a target of confirming every booking within a week of the placement offer.",
                "Flag bookings still unconfirmed inside that window on the tracker.",
                "Keep a standby pool of hosts to absorb late fall-throughs.",
            ],
            "rationale": (
                "The gap into confirmation is where bookings quietly stall. A stated "
                "SLA plus a visible flag turns the stall into a routine checkpoint."
            ),
        },
    },
    ("foyle", "repetition"): {
        "title": "Student documents requested twice",
        "workflow_area": "Document collection",
        "severity": "medium",
        "confidence": 0.85,
        "description": (
            "{count} bookings show a Document Re-request — the same CVs and "
            "letters collected again because the first copies were lost between "
            "seasonal spreadsheets."
        ),
        "fix": {
            "summary": "Single document checklist per booking, carried across seasons",
            "steps": [
                "Track received documents on the booking row itself, not in a per-season sheet.",
                "Carry open bookings forward into the new season's sheet instead of re-entering them.",
                "Only re-request a document when its checklist entry is genuinely empty.",
            ],
            "rationale": (
                "Re-requests happen because each seasonal fork starts blind. One "
                "checklist that travels with the booking removes the reason to ask twice."
            ),
        },
    },
    ("foyle", "rework"): {
        "title": "Placements re-allocated after confirmation",
        "workflow_area": "Placement & hosting",
        "severity": "medium",
        "confidence": 0.8,
        "description": (
            "{count} bookings looped back through Placement Re-allocation — a "
            "host or company fell through after the student was already matched."
        ),
        "fix": {
            "summary": "Confirm host availability before matching, hold a reserve",
            "steps": [
                "Re-confirm host availability at the point of matching, not just at signup.",
                "Keep a small reserve of vetted hosts per season for re-allocations.",
                "Record the fall-through reason so repeat offenders are spotted early.",
            ],
            "rationale": (
                "Each re-allocation redoes matching work under time pressure. Checking "
                "availability up front and holding a reserve absorbs the churn."
            ),
        },
    },
    ("joinery", "delay"): {
        "title": "Jobs waiting on materials before site work",
        "workflow_area": "Materials & scheduling",
        "severity": "high",
        "confidence": 0.85,
        "description": (
            "Site work starts an average of {metric:.0f} days after materials "
            "are ordered — {count} jobs sat over a week waiting on suppliers "
            "while fitters were booked elsewhere."
        ),
        "fix": {
            "summary": "Order long-lead materials at quote acceptance, track lead times",
            "steps": [
                "Order known long-lead items (glass, spray finishing) the day the quote is accepted.",
                "Keep supplier lead times on the materials sheet and schedule site work against them.",
                "Flag any job whose materials are outstanding a week after ordering.",
            ],
            "rationale": (
                "The wait between ordering and starting is dead time the client sees. "
                "Ordering earlier and scheduling against real lead times closes the gap."
            ),
        },
    },
    ("joinery", "repetition"): {
        "title": "Sites measured twice before ordering",
        "workflow_area": "Surveying & quoting",
        "severity": "medium",
        "confidence": 0.85,
        "description": (
            "{count} jobs needed a Re-measure — the original survey didn't "
            "capture what the workshop needed, so someone drove back out."
        ),
        "fix": {
            "summary": "One survey checklist so the first measure is the last",
            "steps": [
                "Use a fixed measure checklist (openings, services, access) on the first visit.",
                "Photograph every opening alongside the measurements.",
                "Have the workshop confirm the cutting list from the survey before ordering.",
            ],
            "rationale": (
                "A second site visit costs half a day each time. A checklist plus photos "
                "gives the workshop what it needs from visit one."
            ),
        },
    },
    ("joinery", "rework"): {
        "title": "Snagging call-backs after handover",
        "workflow_area": "Fitting & handover",
        "severity": "medium",
        "confidence": 0.8,
        "description": (
            "{count} jobs looped back through a Snagging Revisit — defects "
            "found after the fitter had left, forcing an unbilled return trip."
        ),
        "fix": {
            "summary": "Walk the snag list with the client before leaving site",
            "steps": [
                "Do a joint walk-through against the job spec at the end of the fit.",
                "Fix same-day snags before leaving; book anything bigger there and then.",
                "Sign off the walk-through so the invoice can go out immediately.",
            ],
            "rationale": (
                "A revisit costs travel and an unbillable half-day. Catching snags while "
                "still on site turns the call-back into a ten-minute fix."
            ),
        },
    },
}


def build_cases(profile: str) -> list[dict]:
    markers = config.MESSY_PROFILES[profile]["markers"]
    texts = _load_record_texts()
    detected = detect_generic(load_event_log(), **markers)
    detected_at = _utc_now_iso()

    cases: list[dict] = []
    for bn in detected:
        tpl = _TEMPLATES[(profile, bn.type)]
        cases.append({
            "case_id": bn.id,
            "detected_at": detected_at,
            "title": tpl["title"],
            "workflow_area": tpl["workflow_area"],
            "severity": tpl["severity"],
            "confidence": tpl["confidence"],
            "description": tpl["description"].format(metric=bn.metric_value, count=bn.affected_count),
            "evidence": _evidence(bn, texts),
            "suggested_fix": dict(tpl["fix"]),
            "retrieved_resolutions": _retrieved_resolutions(bn),
            "status": "pending",
        })
    return cases


def build_workflow(profile: str) -> dict:
    stage_order = config.MESSY_PROFILES[profile]["stage_order"]
    markers = config.MESSY_PROFILES[profile]["markers"]
    df = load_event_log()
    detected = detect_generic(df, **markers)

    stage_to_bn = {bn.stage: bn for bn in detected if bn.affected_count > 0}
    canon_to_label = {s.lower(): s for s in stage_order}

    present = {s for s in df["stage"].unique() if s in canon_to_label}
    nodes = []
    for label in stage_order:
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
    # Stamp the active SME so the dashboard's branding/KPIs adapt to it.
    ui = config.MESSY_PROFILES[profile].get("ui", {})
    return {"profile": profile, "ui": ui, "nodes": nodes, "edges": edges, "kpis": kpis}


def export(profile: str, cases_path=None, workflow_path=None) -> tuple[int, dict]:
    cases_path = cases_path or config.UI_CASES_PATH
    workflow_path = workflow_path or config.UI_WORKFLOW_PATH

    cases = build_cases(profile)
    workflow = build_workflow(profile)

    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(cases), workflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Export messy-drive bottlenecks as UI cases")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    args = parser.parse_args()

    n, workflow = export(args.profile)
    print(f"Exported {n} {args.profile} UI cases to {config.UI_CASES_PATH.relative_to(config.ROOT)}")
    print(f"Exported workflow ({len(workflow['nodes'])} stages, "
          f"{len(workflow['edges'])} transitions) to {config.UI_WORKFLOW_PATH.relative_to(config.ROOT)}")
    print(f"KPIs: {workflow['kpis']}")


if __name__ == "__main__":
    main()
