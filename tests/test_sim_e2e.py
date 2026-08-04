"""End-to-end: the loop actually closes.

Before the simulator, the world was a fixed recording: an approved fix could
never change what the NEXT analysis saw, so a finding's affected-case count
could only ever grow, and `actions/outcome.py::compare` could never observe an
improvement. This module proves the arrow now exists: an approved action
measurably reduces what the product reports at a later day, and doing nothing
does not.

Two things in the original task brief for this test turned out to be wrong on
inspection, and are fixed here rather than transcribed:

1. The DataFrame the case rules actually read. `detection/case_rules.py`
   groups on `df["case_id"]` and reads `df["stage"]` (see `_case_state`,
   `_unowned_cases`, `_key_person_dependency`, ...) -- there is no `activity`
   column anywhere in that module. A frame built with an `activity` column
   raises `KeyError` the moment `detect_case_findings` runs, which is not a
   subtle bug -- it means the reference implementation in the brief was never
   actually executed.

   A second, easy-to-miss issue sits right behind the first: casing.
   `detection/detect.py::load_event_log` -- the function that hands the case
   rules a DataFrame in the real product -- lower-cases and strips the stage
   text (`df["stage"] = _canon(df["activity"])`, `_canon` = lower + strip),
   and every stage/terminal/revenue comparison inside `case_rules.py`
   (`_is_finished`, `_unrealised_value`'s `stages & revenue`, ...) canonicalises
   its OWN side to lower-case before comparing. Handing it the simulator's
   Title-Case stage strings unchanged (`"Paid"` instead of `"paid"`) silently
   breaks every one of those set intersections -- cases that have plainly
   reached a terminal/revenue stage stop being recognised as finished. `_frame`
   below reproduces the real ingest casing convention exactly, not just the
   column name, so the case rules see what the product actually hands them.

2. Which finding type the test can honestly use. The brief suggested
   `unowned_case`, reasoning that `worker.py::_effect`'s `unowned_case` branch
   might need a fix to assign a non-empty actor. That branch already does the
   right thing -- but `unowned_case` is still the wrong choice, for a
   different reason the brief flagged as a risk to check: `_drift` (step.py)
   calls `worker.advance_case` for every untouched, non-terminal case that
   doesn't roll a stall, and `advance_case` assigns
   `case.owner or rng.choice(_people(world))` -- i.e. ordinary, un-approved
   drift ALSO clears unowned cases, for free, as a side effect of moving them
   on. Measured directly (see the investigation notes in
   `.superpowers/sdd/2026-08-02-simulator-core-p1/task-10-report.md`): with no
   approval at all, the day-0 world's 2 unowned cases drop to 0 within two
   days of drift alone. A control built on `unowned_case` would therefore be
   measuring drift, not intervention -- exactly the failure mode the brief
   warned against. `stage_sla_breach` and `stalled_case` share the same
   problem: their approved effect (`advance_case`) is the literal thing drift
   already does to every untouched case, so drift alone clears 6 of 8
   `stage_sla_breach` cases within a single day on this world.

   `unrealised_value` does not have this problem. Its approved effect
   (`worker.py::_effect`) moves a case straight to the terminal/revenue stage
   in one step -- something ordinary one-stage-at-a-time drift cannot do to a
   case that starts several stages away, and does not do to this world's
   affected cases within a single day: measured, the day-0 world's 16
   unrealised-value cases are the exact same 16 one day later with no
   approval (set-equal, not just same count). With the action approved, the
   same single day clears half of them (16 -> 8, deterministically -- the
   simulator's RNG is seeded per (seed, day), not per process, so this is not
   a flaky draw). `key_person_dependency` was also considered and rejected:
   its finding is monotonically non-decreasing under this world regardless of
   approval, because `affected_cases` accumulates historical events at a
   stage and the approved effect (an extra event by a different actor) does
   not remove a case that already has a top-actor event on record -- so it
   would make a fine second control but cannot demonstrate a reduction at all.

Both tests below run the world for exactly one day, deliberately: it is the
shortest possible window, so it is also the strongest possible control (the
smallest opportunity for anything else -- an organic `payment_made` message, a
new arrival, ordinary drift -- to also move the number) while still being
large enough for the approved effect to show up clearly.
"""

from __future__ import annotations

import json

import pandas as pd

import config
from actions.models import ActionItem
from detection.case_rules import detect_case_findings
from simulator.step import advance
from simulator.world import day0_from_generator

PROFILE_CFG = config.MESSY_PROFILES["advisory"]

FINDING_TYPE = "unrealised_value"


def _frame(world) -> pd.DataFrame:
    """The world as the product would see it after ingest -- the same columns
    AND casing convention `detection/detect.py::load_event_log` produces for a
    real drive, without going through Excel. `stage` is lower-cased/stripped
    to match `_canon()` there; every comparison inside `detection/case_rules.py`
    (terminal/revenue stage membership, SLA stage lookups, ...) assumes that
    convention on its input."""
    rows = [{"case_id": c.cid, "stage": e.stage.strip().lower(), "ts": e.ts,
             "actor": e.actor, "status": e.status,
             "source_ref": f"sim:{c.cid}", "value": c.value}
            for c in world.cases.values() for e in c.events]
    return pd.DataFrame(rows)


def _affected(df: pd.DataFrame, finding_type: str) -> set[str]:
    found = detect_case_findings(df, PROFILE_CFG)
    return {cid for f in found if f.type == finding_type
            for cid in f.affected_cases}


def _item(finding_type: str, case_ids) -> ActionItem:
    return ActionItem(
        action_id="A-1", profile="advisory",
        finding_key=f"{finding_type}::x::y", finding_type=finding_type,
        title="t", summary="s", workflow="Lead-to-cash", stage="Invoice",
        affected_case_ids=sorted(case_ids), status="approved",
        created_at="2026-07-20", updated_at="2026-07-20")


def test_approving_an_action_reduces_the_finding_the_product_reports(tmp_path):
    world = day0_from_generator("advisory")
    before = _affected(_frame(world), FINDING_TYPE)
    assert before, f"expected {FINDING_TYPE} cases in the day-0 world"

    approved = [_item(FINDING_TYPE, before)]
    advance(world, approved, drive_dir=tmp_path / "drive",
            cache_dir=tmp_path / "cache", use_llm=False)

    after = _affected(_frame(world), FINDING_TYPE)
    assert len(after) < len(before), (
        "an approved unrealised_value action must reduce the count the "
        "product reports -- this is the arrow the pre-baked stream never had")


def test_doing_nothing_does_not_reduce_it(tmp_path):
    """The control: without the approval, the count must not fall for the same
    reason. Otherwise the reduction above proves nothing.

    `unowned_case` -- the brief's original suggestion -- fails this control on
    this world (drift alone clears it); see the module docstring. This uses
    the same one-day window as the main test above so the two are a fair,
    like-for-like comparison: same world, same day, only the approval differs."""
    world = day0_from_generator("advisory")
    before = _affected(_frame(world), FINDING_TYPE)
    advance(world, [], drive_dir=tmp_path / "drive",
            cache_dir=tmp_path / "cache", use_llm=False)
    after = _affected(_frame(world), FINDING_TYPE)
    assert after == before, (
        f"{FINDING_TYPE} cases should not clear themselves without "
        "intervention -- one day of arrivals/messages/drift with nothing "
        "approved must leave this exact set of cases unchanged")


def test_the_rendered_drive_is_ingestable_by_the_product(tmp_path):
    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    world = day0_from_generator("advisory")
    advance(world, [], drive_dir=tmp_path / "drive",
            cache_dir=tmp_path / "cache", use_llm=False)

    gt = json.loads(config.MESSY_PROFILES["advisory"]["gt_mapping"]
                    .read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile="advisory", approved_at="2026-01-01T00:00:00+00:00",
        source_proposal_generated_at="2026-01-01T00:00:00+00:00",
        files=[ApprovedFileMapping(**f) for f in gt["files"]])

    events, _docs = read_mapped(tmp_path / "drive", approved)
    assert events, "the simulated drive must read through the approved mapping"
