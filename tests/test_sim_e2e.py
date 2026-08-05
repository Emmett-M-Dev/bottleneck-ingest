"""End-to-end: the loop actually closes.

Before the simulator, the world was a fixed recording: an approved fix could
never change what the NEXT analysis saw, so a finding's affected-case count
could only ever grow, and `actions/outcome.py::compare` could never observe an
improvement. This module proves the arrow now exists: an approved action
measurably reduces what the product reports at a later day, by more than the
untouched world reduces it on its own.

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
   (Fix-round-1 review compared `_frame`'s output against the real
   `read_mapped` -> `load_event_log` path directly and found identical
   affected-case sets for all six finding types -- this fidelity is now
   independently confirmed, not just argued.)

2. Which finding type the test can honestly use. The brief suggested
   `unowned_case`, reasoning that `worker.py::_effect`'s `unowned_case` branch
   might need a fix to assign a non-empty actor. That branch already does the
   right thing -- but `unowned_case` is still the wrong choice, for a
   different reason the brief flagged as a risk to check: `_drift` (step.py)
   calls `worker.advance_case` for every untouched, non-terminal case that
   doesn't roll a stall, and `advance_case` assigns
   `case.owner or rng.choice(_people(world))` -- i.e. ordinary, un-approved
   drift ALSO clears unowned cases, for free, as a side effect of moving them
   on. Measured directly: with no approval at all, the day-0 world's 2
   unowned cases drop to 0 within two days of drift alone. `stage_sla_breach`
   and `stalled_case` share the same problem: their approved effect
   (`advance_case`) is the literal thing drift already does to every
   untouched case.

   `unrealised_value` was chosen instead, and clears far more of its
   affected cases when approved than the untouched world clears on its own
   -- but IMPORTANT: the untouched world is not inert here either, and this
   module used to claim it was (wrongly). THREE organic channels clear
   `unrealised_value` cases with zero approval. These counts were
   RE-MEASURED after the simulator's drift phase gained structural
   repetition/rework (`simulator/step.py::_drift`, F6 in the final-fix-wave
   review -- a stalled case now always consumes two extra rng draws for a
   repetition/rework roll before the day's remaining draws happen, which
   shifts the exact realisation every later roll in the same day sees, even
   though none of the three channels below changed mechanically). Control
   arm instrumented over 150 trials post-change: 165 clearances were
   observed in total -- more than one channel can fire within a single
   trial, which is why 165 > 150 -- attributed as drift 119, `payment_made`
   38, `progress_update` 8 (measured with a standalone scratchpad harness,
   `measure_sim_e2e.py`, NOT committed to the repo, built for this
   re-measurement: `mode_channels` reruns the exact `_frame`/`_affected`
   helpers below against `simulator.step.advance`, attributing each cleared
   case to whichever applied message touched it that day, else drift; see
   task-10-report.md for the original methodology this reproduces). No
   fourth channel appeared in
   this sample -- expected, since repetition/rework can never reach a
   terminal stage (`_repetition_stages`/`_rework_stages` only ever re-enter
   or step back one stage), so it cannot itself become a new route to
   clearing `unrealised_value`; this was checked for explicitly, not
   assumed, and would be called out here prominently if it had appeared:

     (a) `_drift` can walk a case straight through `Invoice -> Paid` in one
         step when it is already sitting at `Invoice` (two of this world's
         16 affected cases -- `NA-1061`, `NA-1062` -- start exactly there).
         A single day's drift clears such a case with probability
         `1 - world.params["stall_prob.Invoice"]` = 0.45 -- a parameter
         fact, unaffected by the drift change, since a single `rng.random()`
         draw is uniform regardless of how many draws preceded it. The
         dominant channel (119 of the 165 clearances).
     (b) `worker.py::apply_message`'s `payment_made` branch performs the
         BYTE-FOR-BYTE IDENTICAL event append as the approved effect
         (`case.add(terminal, world.current_date, case.owner or "", "done")`)
         -- a client can simply report paying, with no action approved at
         all (38 of the 165).
     (c) `worker.py::apply_message`'s `progress_update` branch
         (`simulator/worker.py:76`) calls the same `advance_case` that
         drift uses -- so an ordinary "please move this on" message on a
         case already sitting at `Invoice` also walks it straight to
         `Paid`, again with zero approval (8 of the 165, the smallest of
         the three but not negligible).

   This makes the corrected claim STRONGER, not weaker: the untouched world
   has three independent, unrelated routes to clearing these cases, and the
   approved arm still beats it by a wide, measured margin (below) despite
   all three working against the comparison. So the honest claim is a
   difference in MAGNITUDE, not a difference
   between "changes" and "does not change": sampling many alternate day-1
   RNG realisations of this same day-0 world (60 paired trials, each arm
   given the identical trial-labelled stream so the comparison is
   apples-to-apples -- see `task-10-report.md`, fix-round-1 section, for the
   sampling code and the full distribution) shows the untouched world
   typically clears a small handful of cases on its own (mean ~1.3, up to 3
   in that sample; fix-round-1 review's own independent, larger sample:
   mean 0.72, up to 4), while the approved arm clears far more (mean ~9,
   never fewer than 5 in that sample; review's sample: mean 8.38, never
   fewer than 1) -- and the approved arm beat the control arm in 60/60
   paired trials in the local sample, and 119/120 in review's larger one at
   this same one-day window (the exact outcome of that one trial was not
   reported back; review also reports 120/120 once the window is extended
   to day 3). `key_person_dependency` was
   also considered and rejected as a MAIN-test candidate: its finding is
   monotonically non-decreasing under this world regardless of approval,
   because `affected_cases` accumulates historical events at a stage and the
   approved effect (an extra event by a different actor) does not remove a
   case that already has a top-actor event on record there.

The real (non-sampled) run below uses this world's actual, fixed generator
seed -- deterministic, not chosen for a favourable outcome -- for exactly one
day. RE-MEASURED post-F6 (repetition/rework in `_drift` shifts the rng
stream position from what the untouched/approved arms previously saw, per
the note above): `before=16`, untouched-arm `after=15` (1 cleared -- via one
of the three organic channels above, not zero as the pre-F6 run happened to
land on), approved-arm `after=7` (9 cleared, of which 8 are cases the
worker itself recorded as `outcome == "applied"` -- the 9th cleared via an
organic channel within the SAME day, on top of the approved effect, which is
exactly why the test below asserts `applied <= (before - after)`, a subset,
rather than equality). Both untouched-arm figures (the old 0 and the new 1)
are single samples from the distribution described above, not a structural
guarantee -- which is exactly why the assertions below compare magnitudes
and tie the reduction to the worker's own effect records, rather than
asserting the untouched arm stays exactly static.
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

# The assertion below is on the PAIRED GAP (approved_cleared - control_cleared
# for the SAME RNG stream), so that -- not the two arms' marginal distributions
# -- is the statistic this margin has to be justified against. Fix-round-2
# review's independent 300-trial paired sample (paired the same way this test
# pairs its own two arms -- see task-10-report.md):
#
#   control  mean 1.07  min 0  max 3   {0:73, 1:147, 2:65, 3:15}
#   approved mean 8.61  min 4  max 13
#   gap      mean 7.54  min 1  max 12
#     margin 3 -> fails  2/300 (0.7%)
#     margin 4 -> fails  5/300 (1.7%)   <- shipped
#     margin 5 -> fails 18/300 (6.0%)
#
# RE-MEASURED for the closeout review, on BOTH the pre-change tree (commit
# 292aac4, before F6 added repetition/rework to `_drift`) and the current
# post-change tree, 300 paired trials each, same pairing methodology as
# above (a per-trial-labelled `WorldState.rng_for_day`, both arms of a trial
# sharing that trial's stream so the comparison stays apples-to-apples).
# Own independently-run sample (standalone scratchpad harness,
# `measure_sim_e2e.py`, not committed -- `python measure_sim_e2e.py <repo>
# paired 300`):
#
#   pre-change   control mean 1.18  approved mean 8.51  gap mean 7.33
#                margin 4 -> fails 13/300 (4.3%)
#   post-change  control mean 1.15  approved mean 8.54  gap mean 7.39
#                margin 4 -> fails  5/300 (1.7%)
#
# MAGNITUDE_MARGIN = 4 holds on both trees: both failure rates stay in the
# low single digits, both gap means sit comfortably above the margin
# (~7.3-7.4 vs a threshold of 4), and the qualitative shape from the
# original 300-trial sample above is intact. The two failure counts are not
# identical (13 vs 5) -- that is real sampling noise, not a sign the margin
# is unsafe on one tree and not the other: at a base rate in the low single
# digits, 300 Bernoulli-ish paired trials have real run-to-run variance
# (a fail-count difference this size is within roughly 2-3 standard
# deviations of a shared ~3% true rate, given the trial-to-trial
# correlation this statistic actually has). Stated plainly rather than
# rounded together: the drift-behaviour change (F6) did not break the
# margin, but it also did not leave the tail behaviour byte-identical --
# the honest read is "the margin survived unchanged as a design decision,
# with normal sampling variance in exactly how much headroom it has."
#
# 4 was kept, not raised to 5: the failure rate roughly quadruples between 4
# and 5, so 4 is the better-supported point on this curve, not a threshold
# picked to be comfortable. It is an honestly disclosed ~1.7% chance that,
# on some other RNG realisation of this same day-0 world, this assertion
# would fail even though the underlying causal claim (approving clears more
# than not approving) still holds -- a false negative on the test, not a
# false claim in the docstring.
#
# Brittleness (separate from seed-to-seed variance above): simulating an
# unrelated upstream change to RNG stream consumption by burning N extra
# draws before this test's own draws begin, N=0..30, broke this assertion in
# 1/31 cases (~3%) and left 4/31 more (~13% total) sitting exactly at the
# threshold with zero headroom. So a future change elsewhere in simulator/
# that shifts how many random draws happen before this test's day runs has
# a real, non-trivial chance of flipping this assertion -- and when it does,
# the failure message below will (misleadingly) read as "background
# clearance caught up", not "an unrelated RNG-consumption change moved the
# stream position". Recorded here so a future maintainer chasing a red
# MAGNITUDE_MARGIN failure checks that before concluding the simulator's
# effect model regressed.
MAGNITUDE_MARGIN = 4


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
    """Not just "the count went down" (drift and two kinds of organic
    message -- payment_made, progress_update -- can all do that on their
    own; see module docstring) but that the reduction is CAUSED by the
    worker's recorded effect: every case_id the
    worker logged as `outcome == "applied"` must be a case_id that actually
    left the finding. Disabling `worker.py::_effect` (verified by hand: see
    task-10-report.md fix-round-1 section) leaves `len(after) < len(before)`
    true -- the day still moves some of these cases via ordinary drift/
    messages -- but empties the `applied` set, so the assertion below, unlike
    a bare count comparison, fails when the mechanism under test is gutted."""
    world = day0_from_generator("advisory")
    before = _affected(_frame(world), FINDING_TYPE)
    assert before, f"expected {FINDING_TYPE} cases in the day-0 world"

    approved = [_item(FINDING_TYPE, before)]
    result = advance(world, approved, drive_dir=tmp_path / "drive",
                     cache_dir=tmp_path / "cache", use_llm=False)

    after = _affected(_frame(world), FINDING_TYPE)
    assert len(after) < len(before), (
        "an approved unrealised_value action must reduce the count the "
        "product reports -- this is the arrow the pre-baked stream never had")

    applied = {e["case_id"] for e in result.effects if e["outcome"] == "applied"}
    assert applied, (
        "no case was recorded as applied -- the reduction above (if any) "
        "cannot be attributed to the approved action at all")
    assert applied <= (before - after), (
        "every case_id the worker recorded as applied must be a case that "
        "actually left the finding -- otherwise the reduction could be "
        "coming from drift or an organic message, not the approval")


def test_the_untouched_world_clears_far_fewer_cases_than_the_approved_action(tmp_path):
    """The comparison that matters: not "the untouched world is static" (it
    is not -- see module docstring) but that approving the action clears
    MANY MORE cases than leaving the world alone does, by a wide, evidence-
    based margin (`MAGNITUDE_MARGIN`). Both arms start from the identical
    day-0 world (the generator's seed is fixed, so two independent
    `day0_from_generator` calls produce provably identical starting state --
    asserted below, not assumed) and are each advanced exactly one day, so
    this is a fair, like-for-like comparison: same starting world, same
    length of time, only the approval differs."""
    world_ctrl = day0_from_generator("advisory")
    before_ctrl = _affected(_frame(world_ctrl), FINDING_TYPE)

    world_main = day0_from_generator("advisory")
    before_main = _affected(_frame(world_main), FINDING_TYPE)
    assert before_ctrl == before_main, (
        "both arms must start from the identical day-0 world")

    advance(world_ctrl, [], drive_dir=tmp_path / "drive_ctrl",
            cache_dir=tmp_path / "cache_ctrl", use_llm=False)
    after_ctrl = _affected(_frame(world_ctrl), FINDING_TYPE)
    control_cleared = len(before_ctrl - after_ctrl)

    approved = [_item(FINDING_TYPE, before_main)]
    advance(world_main, approved, drive_dir=tmp_path / "drive_main",
            cache_dir=tmp_path / "cache_main", use_llm=False)
    after_main = _affected(_frame(world_main), FINDING_TYPE)
    approved_cleared = len(before_main - after_main)

    assert approved_cleared >= control_cleared + MAGNITUDE_MARGIN, (
        f"approved clearance ({approved_cleared}) must beat the untouched "
        f"world's own background clearance ({control_cleared}, via drift "
        f"and/or organic payment_made / progress_update messages -- see "
        f"module docstring) by a wide margin, not merely exceed it: got a "
        f"gap of {approved_cleared - control_cleared}, needed at least "
        f"{MAGNITUDE_MARGIN}")


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
    # Not just "some events came through" -- every case in the simulated
    # world must round-trip through the approved mapping with none dropped
    # and none fabricated (measured post-F6: 190 events -- up from the
    # pre-F6 186, since repetition/rework append extra event rows to
    # stalled cases -- 27/27 cases, exact match).
    ingested_case_ids = {e["case_id"] for e in events}
    assert ingested_case_ids == set(world.cases), (
        "the rendered drive must be ingestable for every case the "
        "simulator holds, not just some of them")
