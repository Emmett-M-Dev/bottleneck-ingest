"""Export messy-drive bottlenecks as UI cases, per SME profile.

    python -m bridge.export_messy --profile foyle              # RAG diagnosis
    python -m bridge.export_messy --profile foyle --offline    # templates only

Detection is DYNAMIC (detection.dynamic.detect_dynamic): every stage is
scanned for outlier delays, duplicate entries and backward loops, so the
export carries 0..N bottleneck cases — the count is a property of the data.

Per bottleneck the RAG diagnosis agent (pipeline/diagnose.py, Claude) supplies
the description, suggested fix, confidence and retrieved past resolutions;
type-generic templates below are the fallback on any failure or offline
(--offline flag, DIAGNOSE_OFFLINE=1 env, or a missing API key), so the export
never breaks.

Every case also carries:
  estimated_cost — the profile's cost model (config MESSY_PROFILES[p]["costs"])
                   applied to the detected metric, with the basis spelled out
  llm_payload    — the EXACT scrubbed payload the diagnosis agent sends (built
                   even offline; no API call needed) — the dashboard's
                   "what the AI saw" evidence
  type / stage   — so the UI can badge cards and the eval can calibrate

Each export appends a snapshot to outputs/history_<profile>.jsonl — the
honest time series behind the dashboard's impact sparklines.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import config
from bridge.export_cases import _evidence, _load_record_texts, _utc_now_iso
from detection.detect import finding_key, load_event_log
from detection.dynamic import detect_dynamic

# Type-generic fallbacks — stage is interpolated, so they cover ANY stage the
# dynamic detector flags (the old per-profile template dict could not).
_TYPE_FALLBACKS: dict[str, dict] = {
    "delay": {
        "title": "Cases stall entering {stage}",
        "description": (
            "{count} cases waited an average of {metric:.0f} days to reach "
            "{stage} — far beyond the workflow's normal rhythm."
        ),
        "fix": {
            "summary": "Set an entry SLA for {stage}",
            "steps": [
                "Agree a target number of days for work to reach {stage}.",
                "Flag cases still short of {stage} inside that window on the tracker.",
                "Review flagged cases weekly and clear the blocker behind each one.",
            ],
            "rationale": (
                "The gap into {stage} is where cases quietly stall. A stated "
                "target plus a visible flag turns the stall into a routine checkpoint."
            ),
        },
    },
    "repetition": {
        "title": "{stage} done twice",
        "description": (
            "{count} cases passed through {stage} more than once — the same "
            "step is being redone instead of carried forward."
        ),
        "fix": {
            "summary": "Make the first {stage} the only one",
            "steps": [
                "Use a fixed checklist for {stage} so the first pass captures everything.",
                "Record the outcome of {stage} on the case row itself, visible to everyone.",
                "Only repeat {stage} when its recorded outcome is genuinely missing.",
            ],
            "rationale": (
                "Repeats happen because the first pass isn't trusted or isn't "
                "visible. One recorded, checklisted pass removes the reason to redo it."
            ),
        },
    },
    "rework": {
        "title": "Cases loop back to {stage}",
        "description": (
            "{count} cases returned to {stage} after having moved past it — "
            "earlier work is being redone under time pressure."
        ),
        "fix": {
            "summary": "Confirm before leaving {stage}, hold a reserve",
            "steps": [
                "Re-confirm the inputs to {stage} at the point of completion, not just at the start.",
                "Keep a small reserve (people, slots or stock) to absorb fall-throughs.",
                "Record the reason for every loop-back so repeat causes are spotted early.",
            ],
            "rationale": (
                "Each loop-back redoes finished work. Confirming up front and "
                "holding a reserve absorbs the churn instead of re-planning it."
            ),
        },
    },
}


def _canon(text: str) -> str:
    """Canonicalise text for key derivation: strip and lowercase."""
    return str(text or "").strip().lower()


def _severity(bn, total_cases: int) -> str:
    share = bn.affected_count / total_cases if total_cases else 0.0
    if bn.type == "delay":
        return "high" if (share >= 0.25 or bn.metric_value >= 12) else "medium"
    if share >= 0.25:
        return "high"
    return "medium" if share >= 0.1 else "low"


def _estimated_cost(bn, profile: str) -> dict | None:
    costs = config.MESSY_PROFILES[profile].get("costs")
    if not costs:
        return None
    cur = costs["currency"]
    if bn.type == "delay":
        amount = round(bn.affected_count * bn.metric_value * costs["delay_day_cost"])
        basis = (f"{bn.affected_count} cases × {bn.metric_value:.0f} days × "
                 f"{cur}{costs['delay_day_cost']}/day")
    elif bn.type == "repetition":
        amount = bn.affected_count * costs["repetition_event_cost"]
        basis = f"{bn.affected_count} repeated steps × {cur}{costs['repetition_event_cost']}"
    else:  # rework
        amount = bn.affected_count * costs["rework_loop_cost"]
        basis = f"{bn.affected_count} loop-backs × {cur}{costs['rework_loop_cost']}"
    return {"amount": int(amount), "currency": cur, "basis": basis}


def build_cases(profile: str, offline: bool = False, client=None) -> list[dict]:
    from pipeline.diagnose import _build_payload  # loads spaCy for the scrub

    texts = _load_record_texts()
    df = load_event_log()
    stage_order = config.MESSY_PROFILES[profile]["stage_order"]
    detected = detect_dynamic(df, stage_order)
    total_cases = int(df["case_id"].nunique())
    detected_at = _utc_now_iso()
    offline = offline or os.environ.get("DIAGNOSE_OFFLINE") == "1"

    cases: list[dict] = []
    for bn in detected:
        tpl = _TYPE_FALLBACKS[bn.type]
        evidence = _evidence(bn, texts)
        bn_dict = {"id": bn.id, "type": bn.type, "stage": bn.stage,
                   "metric_label": bn.metric_label, "metric_value": bn.metric_value,
                   "affected_count": bn.affected_count,
                   "evidence_excerpts": [e["excerpt"] for e in evidence]}
        # Template-built case first — the guaranteed-valid fallback shape.
        fmt = {"stage": bn.stage, "count": bn.affected_count,
               "metric": bn.metric_value}
        case = {
            "case_id": bn.id,
            "finding_key": finding_key(bn),
            "type": bn.type,
            "stage": bn.stage,
            "detected_at": detected_at,
            "title": tpl["title"].format(**fmt),
            "workflow_area": bn.stage,
            "severity": _severity(bn, total_cases),
            "confidence": 0.8,
            "description": tpl["description"].format(**fmt),
            "evidence": evidence,
            "suggested_fix": {
                "summary": tpl["fix"]["summary"].format(**fmt),
                "steps": [s.format(**fmt) for s in tpl["fix"]["steps"]],
                "rationale": tpl["fix"]["rationale"].format(**fmt),
            },
            "retrieved_resolutions": [],
            "estimated_cost": _estimated_cost(bn, profile),
            "llm_payload": _build_payload(bn_dict, profile, []),
            "status": "pending",
        }
        if not offline:
            try:
                from pipeline.diagnose import diagnose, retrieve_resolutions

                retrieved = retrieve_resolutions(
                    f"{bn.stage} {bn.type} bottleneck: {case['title']}", profile)
                result = diagnose(bn_dict, profile, retrieved=retrieved, client=client)
                case["description"] = f"{result.diagnosis} Root cause: {result.root_cause}"
                case["suggested_fix"] = result.suggested_fix.model_dump()
                case["confidence"] = round(min(1.0, max(0.0, result.confidence)), 2)
                case["retrieved_resolutions"] = [
                    {k: r[k] for k in ("resolution_id", "source",
                                       "similarity_score", "summary")}
                    for r in retrieved]
                # The payload the model ACTUALLY saw (with the retrieval).
                case["llm_payload"] = _build_payload(bn_dict, profile, retrieved)
            except Exception as exc:
                print(f"[diagnose] {bn.id}: fell back to the authored template ({exc})")
        cases.append(case)

    return cases


def build_workflow(profile: str) -> dict:
    stage_order = config.MESSY_PROFILES[profile]["stage_order"]
    df = load_event_log()
    detected = detect_dynamic(df, stage_order)

    # Several bottlenecks can now share a stage — the node shows the biggest.
    stage_to_bn: dict[str, object] = {}
    for bn in detected:
        cur = stage_to_bn.get(bn.stage)
        if cur is None or bn.affected_count > cur.affected_count:
            stage_to_bn[bn.stage] = bn
    canon_to_label = {s.lower(): s for s in stage_order}

    # Which drive sheet(s) feed each stage — the source_ref is "<file>:<sheet>:<row>".
    files = df["source_ref"].fillna("").str.split(":").str[0]
    stage_sources: dict[str, set[str]] = {}
    for stage, fname in zip(df["stage"], files):
        if fname:
            stage_sources.setdefault(stage, set()).add(fname)

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
            "sources": sorted(stage_sources.get(label.lower(), set())),
        })

    transitions: dict[tuple[str, str], int] = {}
    for _, g in df.sort_values("ts").groupby("case_id"):
        seq = [canon_to_label[s] for s in g["stage"] if s in canon_to_label]
        for a, b in zip(seq, seq[1:]):
            if a != b:
                transitions[(a, b)] = transitions.get((a, b), 0) + 1
    edges = [{"from": a, "to": b, "count": c} for (a, b), c in sorted(transitions.items())]

    affected_cases = {c for bn in detected for c in bn.affected_cases}
    total_cost = sum((_estimated_cost(bn, profile) or {}).get("amount", 0)
                     for bn in detected)
    currency = config.MESSY_PROFILES[profile].get("costs", {}).get("currency", "£")
    kpis = {
        "cases": int(df["case_id"].nunique()),
        "events": int(len(df)),
        "open_bottlenecks": len(detected),
        "cases_affected": len(affected_cases),
        "total_estimated_cost": int(total_cost),
        "currency": currency,
    }
    # Stamp the active SME so the dashboard's branding/KPIs adapt to it.
    ui = config.MESSY_PROFILES[profile].get("ui", {})
    return {"profile": profile, "ui": ui, "nodes": nodes, "edges": edges, "kpis": kpis}


def history_path(profile: str):
    return config.OUTPUTS / f"history_{profile}.jsonl"


def _messy_cells(profile: str) -> int | None:
    """Cells still needing normalisation, from the last remediation scan."""
    plan_path = config.OUTPUTS / f"ui_remediation_{profile}.json"
    if not plan_path.exists():
        return None
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return sum(v["count"] for f in plan.get("files", [])
               for v in f.get("value_map", [])
               if v["original"].strip() != v["canonical"])


def append_history(profile: str, cases: list[dict], workflow: dict,
                   event: str = "export") -> None:
    """One honest snapshot per pipeline run — the impact sparklines' data."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "kpis": workflow["kpis"],
        "bottlenecks": [
            {"case_id": c["case_id"], "type": c["type"], "stage": c["stage"],
             "severity": c["severity"],
             "estimated_cost": (c.get("estimated_cost") or {}).get("amount")}
            for c in cases if c["type"] != "anomaly"
        ],
        "messy_cells": _messy_cells(profile),
    }
    path = history_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def export(profile: str, cases_path=None, workflow_path=None,
           offline: bool = False) -> tuple[int, dict]:
    cases_path = cases_path or config.UI_CASES_PATH
    workflow_path = workflow_path or config.UI_WORKFLOW_PATH

    cases = build_cases(profile, offline=offline)
    workflow = build_workflow(profile)

    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    append_history(profile, cases, workflow)
    return len(cases), workflow


def main() -> None:
    from pipeline.diagnose import load_dotenv
    load_dotenv()  # picks up ANTHROPIC_API_KEY from the repo's .env

    parser = argparse.ArgumentParser(description="Export messy-drive bottlenecks as UI cases")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    parser.add_argument("--offline", action="store_true",
                        help="skip the RAG diagnosis + anomaly pass; templates only")
    args = parser.parse_args()

    n, workflow = export(args.profile, offline=args.offline)
    print(f"Exported {n} {args.profile} UI cases to {config.UI_CASES_PATH.relative_to(config.ROOT)}")
    print(f"Exported workflow ({len(workflow['nodes'])} stages, "
          f"{len(workflow['edges'])} transitions) to {config.UI_WORKFLOW_PATH.relative_to(config.ROOT)}")
    print(f"KPIs: {workflow['kpis']}")


if __name__ == "__main__":
    main()
