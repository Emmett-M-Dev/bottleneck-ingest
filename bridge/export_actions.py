"""Build and export the action queue — the primary worker-facing artefact.

    python -m bridge.export_actions --profile advisory
    python -m bridge.export_actions --profile advisory --review   # + outcome review

Writes three things:

    outputs/actions_<profile>.json      the standing queue (merged, so a
                                        worker's owner/due-date/progress
                                        survives re-analysis)
    outputs/ui_actions_<profile>.json   the dashboard's read model — the queue
                                        grouped into sections, plus the
                                        intervention board and the counts
    outputs/snapshots_<profile>.jsonl   this run's measurable state, appended

and, with --review, measures every completed intervention against the snapshot
this run just took, so "did it work?" is answered by the pipeline rather than
by whoever remembers.

The read model is deliberately a separate file from the store. The store is
state the worker owns; `ui_actions_*.json` is a derived view the dashboard can
throw away and rebuild. Nothing in the UI file is a source of truth.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone

import config
from actions import store
from actions.build import build_action_items, build_snapshot, data_quality_confidence
from actions.execute import _assert_snapshot_is_clean, execution_reason, route
from actions.models import ActionItem
from actions.outcome import review as review_outcomes
from actions.outcome import summarise
from actions.rank import rank
from actions.templates import case_noun, workflow_name

# The sections the "Today" view is built from, in the order a worker should
# read them. Each is a predicate over a ranked item — no item appears twice,
# and `needs_attention` is whatever is left, so nothing can fall off the page.
_MAX_PER_SECTION = 12


def _analysis_date(items: list[ActionItem]) -> str:
    dates = [i.created_at for i in items if i.created_at]
    return max(dates) if dates else date.today().isoformat()


def _section(items: list[ActionItem], predicate) -> list[dict]:
    return [_ui_item(i) for i in items if predicate(i)][:_MAX_PER_SECTION]


def _ui_item(item: ActionItem) -> dict:
    """One queue row, flattened for the dashboard.

    `execution` is the important addition: it tells the worker, before they
    click approve, whether approving changes a file or creates a task. A system
    that can edit spreadsheets should never leave that ambiguous."""
    payload = item.model_dump()
    payload["retrieved_resolutions"] = item.retrieved_resolutions
    payload["execution"] = {
        "route": route(item),
        "will_modify_files": item.is_machine_executable,
        "explanation": execution_reason(item),
    }
    payload["sent_to_llm"] = {
        "used_llm": item.llm_payload is not None,
        "payload": item.llm_payload,
        "note": ("Only the scrubbed payload shown here left this machine. "
                 "Names and identifiers are replaced with placeholders before "
                 "anything is sent." if item.llm_payload else
                 "Nothing about this item was sent to a language model."),
    }
    return payload


# Provenance has three states and the UI must be able to tell them apart:
#   ""        the profile's own default drive — trusted, no banner
#   "<path>"  an alternate drive — banner names it
#   unknown   a snapshot written before provenance was recorded. approve and
#             review refuse it (actions/execute.py fails closed), so the UI has
#             to warn rather than render it as clean.
UNKNOWN_SOURCE_DRIVE = "unknown (snapshot predates provenance tracking)"


def _ui_source_drive(snapshot) -> str:
    if snapshot is None:
        return ""
    return UNKNOWN_SOURCE_DRIVE if snapshot.source_drive is None else snapshot.source_drive


def build_ui_actions(profile: str, items: list[ActionItem],
                     *, snapshot=None) -> dict:
    """The dashboard read model: sections, totals and the intervention board."""
    currency = (config.MESSY_PROFILES[profile].get("costs") or {}).get("currency", "£")
    interventions = store.load_interventions(profile)
    open_statuses = ("approved", "assigned", "in_progress", "completed",
                     "outcome_review")

    live = [i for i in items if i.status in ("proposed", "approved", "assigned",
                                             "in_progress")]
    revenue = [i for i in live if (i.impact.revenue_at_risk or 0) > 0]
    delivery = [i for i in live if i.impact.category == "delivery_risk"
                or i.impact.delivery_risk in ("high", "medium")]
    capacity = [i for i in live if i.impact.category == "capacity_loss"]
    data = [i for i in live if i.action_category == "data_quality"]

    claimed: set[str] = set()

    def unclaimed(pool: list[ActionItem]) -> list[dict]:
        out = []
        for item in pool:
            if item.action_id in claimed:
                continue
            claimed.add(item.action_id)
            out.append(_ui_item(item))
            if len(out) >= _MAX_PER_SECTION:
                break
        return out

    # Order matters — an item is claimed by the FIRST section it matches, and
    # capacity findings carry a delivery risk too, so capacity must get first
    # refusal or every key-person and overloaded-owner item files itself under
    # "delivery risks" and the capacity section renders empty.
    sections = {
        "revenue_at_risk": unclaimed(revenue),
        "capacity_constraints": unclaimed(capacity),
        "delivery_risks": unclaimed(delivery),
        "data_quality": unclaimed(data),
    }
    sections["needs_attention"] = unclaimed(live)

    validated = [i for i in interventions if i.is_trusted_knowledge]
    validated.sort(key=lambda i: (i.outcome.validated_at or ""), reverse=True)

    return {
        "profile": profile,
        "workflow": workflow_name(profile),
        "case_noun": case_noun(profile),
        "ui": config.MESSY_PROFILES[profile].get("ui", {}),
        "currency": currency,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_date": _analysis_date(items),
        "snapshot_id": snapshot.snapshot_id if snapshot else None,
        # "" for the profile's own default drive; non-empty means this queue
        # was built from an alternate drive (a later real snapshot, or the
        # demo simulator's synthetic week) — the dashboard's TodayTab shows an
        # amber strip naming it, so a simulated queue is never mistaken for
        # the SME's live spreadsheets. See CLAUDE.md §4b / actions/execute.py.
        #
        # A snapshot predating source_drive carries None, meaning provenance is
        # UNKNOWN. That must not serialise as null: null is falsy in JS, so the
        # banner would render the queue as clean while approve/review refuse it
        # at the button — the worst combination. Surface it as a string the
        # banner will show, matching the fail-closed rule on the Python side.
        "source_drive": _ui_source_drive(snapshot),
        "totals": {
            "open_items": len(live),
            "revenue_at_risk": round(sum(i.impact.revenue_at_risk or 0
                                         for i in live), 2),
            "cost_incurred": round(sum(i.impact.cost_incurred or 0
                                       for i in live), 2),
            "hours_at_risk": round(sum(i.impact.hours_at_risk or 0
                                       for i in live), 1),
            "cases_affected": len({c for i in live for c in i.affected_case_ids}),
            "by_category": {
                category: sum(1 for i in live if i.action_category == category)
                for category in ("case_action", "process_intervention",
                                 "data_quality")},
            "data_quality_confidence": next(
                (i.data_quality_confidence for i in items
                 if i.data_quality_confidence is not None), None),
        },
        "sections": sections,
        "interventions": {
            "active": [i.model_dump() for i in interventions
                       if i.status in open_statuses][:25],
            "recently_validated": [i.model_dump() for i in validated][:10],
            "summary": summarise(profile),
        },
    }


class WrongProfileError(RuntimeError):
    """The event log on disk belongs to a different SME."""


def _assert_event_log_belongs_to(profile: str) -> None:
    """Refuse to build a queue out of another SME's events.

    `outputs/event_log.parquet` is one global file that each profile overwrites
    in turn, so an unguarded build silently produces a queue whose branding says
    one business and whose findings belong to another. Ingestion stamps the
    owner; this is the check that makes the stamp worth writing.

    The stamp is `profile` for a plain ingest, or `f"{profile}@{drive}"` when
    `--drive` pointed somewhere other than the profile's configured folder — a
    later real snapshot (`messy_advisory_followup/`) or the simulator's
    `data/sim/<profile>/drive/` (`ingest.py::_write_event_log_owner`). Only the
    profile part of the stamp is the identity check here: `--drive` re-analysis
    of the SAME profile is a documented, legitimate workflow (CLAUDE.md §4b —
    it's what makes `validated` outcomes reachable at all), not a cross-profile
    mismatch. We still warn on the alternate-drive case so a user building a
    queue off non-default data notices, but we never block it.

    Reads via `config.read_event_log_owner` (not `ingest`'s own copy) so this
    check never drags ingest.py's embedding/chroma stack into a build() call
    that would otherwise stay light until diagnosis actually needs it."""
    owner_profile, drive = config.read_event_log_owner()
    if not owner_profile:
        return
    owner = f"{owner_profile}@{drive}" if drive else owner_profile
    if owner_profile != profile:
        raise WrongProfileError(
            f"The event log currently holds '{owner}' data, not '{profile}'. "
            f"Re-ingest first:  python ingest.py --source messy --profile {profile}")
    if drive:
        print(f"Note: building the '{profile}' queue from an alternate drive "
              f"({drive}), not the profile's default static folder.")


def build(profile: str, *, cases: list[dict] | None = None,
          as_of=None, do_review: bool = False) -> tuple[list[ActionItem], dict]:
    """One analysis run's worth of queue, merged into the standing store."""
    from detection.detect import load_event_log

    _assert_event_log_belongs_to(profile)
    df = load_event_log()
    if cases is None:
        cases_path = config.OUTPUTS / f"ui_cases_{profile}.json"
        if not cases_path.exists():
            cases_path = config.UI_CASES_PATH
        try:
            loaded = json.loads(cases_path.read_text(encoding="utf-8-sig"))
            # Only reuse the export if it belongs to this profile's workflow —
            # ui_cases.json is overwritten by whichever profile ran last.
            stages = {s.lower() for s in config.MESSY_PROFILES[profile]["stage_order"]}
            cases = [c for c in loaded
                     if str(c.get("stage", "")).lower() in stages]
        except (OSError, json.JSONDecodeError):
            cases = []

    fresh = build_action_items(profile, df, cases=cases, as_of=as_of)
    merged = store.merge_actions(store.load_actions(profile), fresh)
    ranked = rank(merged, today=date.fromisoformat(_analysis_date(merged)[:10]))
    snapshot = build_snapshot(profile, df, fresh, as_of=as_of)

    # Check provenance BEFORE anything is written. review_outcomes refuses a
    # snapshot of unknown or alternate-drive origin, and until this check moved
    # up here that refusal landed mid-build: the action store and the snapshot
    # log were already updated while ui_actions_<p>.json was not, so the
    # dashboard showed a stale queue over a newer store.
    if do_review:
        _assert_snapshot_is_clean(profile, snapshot, context="observation")

    store.save_actions(profile, ranked)
    store.append_snapshot(profile, snapshot)

    if do_review:
        review_outcomes(profile, snapshot)

    ui = build_ui_actions(profile, ranked, snapshot=snapshot)
    out = config.OUTPUTS / f"ui_actions_{profile}.json"
    out.write_text(json.dumps(ui, ensure_ascii=False, indent=2), encoding="utf-8")
    return ranked, ui


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the prioritised action queue for one SME profile")
    parser.add_argument("--profile", required=True,
                        choices=sorted(config.MESSY_PROFILES))
    parser.add_argument("--review", action="store_true",
                        help="also measure completed interventions against this run")
    args = parser.parse_args()

    items, ui = build(args.profile, do_review=args.review)
    from detection.detect import load_event_log
    dq, why = data_quality_confidence(load_event_log())

    totals = ui["totals"]
    cur = ui["currency"]
    print(f"\n{ui['ui'].get('brand', args.profile)} — {ui['workflow']} "
          f"(analysis date {ui['analysis_date'][:10]})")
    print(f"{totals['open_items']} open action(s) across "
          f"{totals['cases_affected']} {ui['case_noun']}(s)")
    print(f"  revenue at risk   {cur}{totals['revenue_at_risk']:,.0f}")
    print(f"  cost incurred     {cur}{totals['cost_incurred']:,.0f}")
    print(f"  hours at risk     {totals['hours_at_risk']:g}")
    print(f"  by category       {totals['by_category']}")
    print(f"  data confidence   {dq:.0%} ({why})")
    print("\nTop of the queue:")
    for item in items[:6]:
        money = item.impact.monetary_total
        tag = "projected" if item.impact.is_projection else "measured"
        print(f"  [{item.rank_score:6.1f}] {item.title}")
        print(f"           {item.action_category:21s} "
              f"{cur}{money:,.0f} {tag}  "
              f"{len(item.affected_case_ids)} {ui['case_noun']}(s)  "
              f"due {item.due_date}")
    print(f"\n-> outputs/ui_actions_{args.profile}.json  "
          f"(+ actions_, snapshots_)")


if __name__ == "__main__":
    main()
