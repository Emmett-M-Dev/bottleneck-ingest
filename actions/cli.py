"""Command-line surface for the action layer — the API's only way in.

    python -m actions.cli queue    --profile advisory [--review]
    python -m actions.cli decide   --profile advisory --action-id ACT-... \
                                   --decision approve --owner "Niamh" --due-date 2026-07-28
    python -m actions.cli progress --profile advisory --action-id ACT-... --status in_progress
    python -m actions.cli review   --profile advisory
    python -m actions.cli validate --profile advisory --intervention-id INT-... --effective yes

Every subcommand prints a single JSON object on stdout and nothing else, so the
FastAPI orchestrator can parse it without guessing. Errors exit non-zero with
`{"error": ...}`.

This module stays free of chromadb / torch (the process-isolation rule), which
is why promoting validated knowledge into the vector store is a SEPARATE
command — `python -m pipeline.learn --profile <p> --promote` — that the API
fires in its own process.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import config
from actions import execute as ax
from actions import lifecycle, store
from actions.models import ActionItem
from actions.outcome import review as review_outcomes
from actions.outcome import summarise, validate

_PROGRESS_STATES = ("assigned", "in_progress", "completed")
_DECISIONS = ("approve", "reject", "dismiss")


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _fail(message: str) -> None:
    _emit({"ok": False, "error": message})
    sys.exit(1)


def _find(profile: str, action_id: str) -> tuple[list[ActionItem], ActionItem]:
    items = store.load_actions(profile)
    found = next((i for i in items if i.action_id == action_id), None)
    if found is None:
        _fail(f"no action {action_id} in the {profile} queue — rebuild it with "
              f"`python -m bridge.export_actions --profile {profile}`")
    return items, found


def cmd_queue(args) -> None:
    from bridge.export_actions import build

    items, ui = build(args.profile, do_review=args.review)
    _emit({"ok": True, "profile": args.profile, "items": len(items),
           "totals": ui["totals"], "analysis_date": ui["analysis_date"]})


def cmd_decide(args) -> None:
    items, item = _find(args.profile, args.action_id)
    snapshot = store.latest_snapshot(args.profile)

    if args.decision == "approve":
        intervention, outcome = ax.approve(
            args.profile, item, snapshot=snapshot, owner=args.owner,
            due_date=args.due_date, decision_id=args.decision_id,
            note=args.note)
        result = {"intervention_id": intervention.intervention_id,
                  "status": intervention.status,
                  "baseline_value": intervention.baseline_value,
                  "success_metric": intervention.success_metric,
                  "review_date": intervention.review_date,
                  "execution": outcome}
    else:
        intervention = ax.reject(args.profile, item, reason=args.note,
                                 dismissed=args.decision == "dismiss")
        result = {"intervention_id": intervention.intervention_id,
                  "status": intervention.status,
                  "execution": {"executed": False, "mode": "none",
                                "reason": "The item was declined; nothing ran."}}

    store.save_actions(args.profile, items)
    _emit({"ok": True, "profile": args.profile, "action_id": item.action_id,
           "decision": args.decision, **result})


def cmd_progress(args) -> None:
    items, item = _find(args.profile, args.action_id)
    if not item.intervention_id:
        _fail(f"{item.action_id} has no intervention — approve it first")

    intervention = store.find_intervention(args.profile, item.intervention_id)
    if intervention is None:
        _fail(f"intervention {item.intervention_id} is missing from the store")

    if args.owner:
        intervention.owner = item.owner = args.owner
    if args.due_date:
        intervention.due_date = item.due_date = args.due_date
    try:
        lifecycle.transition(intervention, args.status, note=args.note)
    except lifecycle.TransitionError as exc:
        _fail(str(exc))

    store.upsert_intervention(args.profile, intervention)
    item.status = intervention.status
    item.updated_at = intervention.updated_at
    store.save_actions(args.profile, items)
    _emit({"ok": True, "profile": args.profile, "action_id": item.action_id,
           "intervention_id": intervention.intervention_id,
           "status": intervention.status, "owner": intervention.owner,
           "due_date": intervention.due_date})


def cmd_review(args) -> None:
    snapshot = store.latest_snapshot(args.profile)
    if snapshot is None:
        _fail(f"no analysis snapshot for {args.profile} — run "
              f"`python -m bridge.export_actions --profile {args.profile}` first")
    changed = review_outcomes(args.profile, snapshot)
    _emit({"ok": True, "profile": args.profile,
           "snapshot_id": snapshot.snapshot_id,
           "measured": [{"intervention_id": i.intervention_id,
                         "title": i.title,
                         "status": i.status,
                         "baseline_value": i.baseline_value,
                         "observed_value": i.outcome.observed_value,
                         "expected_value": i.outcome.expected_value,
                         "percent_change": i.outcome.percent_change,
                         "effective": i.outcome.effective,
                         "reason": i.outcome.effective_reason}
                        for i in changed],
           "summary": summarise(args.profile)})


def cmd_validate(args) -> None:
    try:
        intervention = validate(args.profile, args.intervention_id,
                                effective=args.effective == "yes",
                                validated_by=args.by, note=args.note)
    except KeyError as exc:
        _fail(str(exc))

    # Reflect the verdict on the queue row so the UI does not need to join.
    items = store.load_actions(args.profile)
    for item in items:
        if item.intervention_id == intervention.intervention_id:
            item.status = intervention.status
            item.updated_at = datetime.now(timezone.utc).isoformat()
    store.save_actions(args.profile, items)

    _emit({"ok": True, "profile": args.profile,
           "intervention_id": intervention.intervention_id,
           "status": intervention.status,
           "trusted_knowledge": intervention.is_trusted_knowledge,
           "note": ("This fix is now trusted guidance and will be retrieved "
                    "for similar problems once it is embedded."
                    if intervention.is_trusted_knowledge else
                    "Recorded. It stays in the audit trail and will never be "
                    "offered as guidance."),
           "summary": summarise(args.profile)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Action-queue operations")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_profile(p):
        p.add_argument("--profile", required=True,
                       choices=sorted(config.MESSY_PROFILES))
        return p

    q = add_profile(sub.add_parser("queue", help="rebuild the action queue"))
    q.add_argument("--review", action="store_true",
                   help="also measure completed interventions against this run")
    q.set_defaults(func=cmd_queue)

    d = add_profile(sub.add_parser("decide", help="Gate 2: approve/reject/dismiss"))
    d.add_argument("--action-id", required=True)
    d.add_argument("--decision", required=True, choices=_DECISIONS)
    d.add_argument("--owner", default=None)
    d.add_argument("--due-date", default=None)
    d.add_argument("--note", default=None)
    d.add_argument("--decision-id", default=None)
    d.set_defaults(func=cmd_decide)

    p = add_profile(sub.add_parser("progress", help="move tracked work along"))
    p.add_argument("--action-id", required=True)
    p.add_argument("--status", required=True, choices=_PROGRESS_STATES)
    p.add_argument("--owner", default=None)
    p.add_argument("--due-date", default=None)
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_progress)

    r = add_profile(sub.add_parser("review", help="measure completed interventions"))
    r.set_defaults(func=cmd_review)

    v = add_profile(sub.add_parser("validate", help="confirm or overrule an outcome"))
    v.add_argument("--intervention-id", required=True)
    v.add_argument("--effective", required=True, choices=("yes", "no"))
    v.add_argument("--by", default="human")
    v.add_argument("--note", default=None)
    v.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
