import random

from actions.models import ActionItem
from simulator import step as step_mod
from simulator import worker as worker_mod
from simulator.profiles import profile_config
from simulator.step import advance
from simulator.world import day0_from_generator

CFG = profile_config("advisory")


def _run(tmp_path, days=3, approved=()):
    w = day0_from_generator("advisory")
    results = []
    for _ in range(days):
        results.append(advance(w, list(approved), drive_dir=tmp_path / "drive",
                               cache_dir=tmp_path / "cache", use_llm=False))
    return w, results


def test_advance_moves_the_clock_and_renders(tmp_path):
    w, results = _run(tmp_path, days=2)
    assert w.day == 2
    assert [r.day for r in results] == [1, 2]
    assert (tmp_path / "drive" / "leads.xlsx").exists()


def test_events_written_during_a_day_carry_that_day(tmp_path):
    w, _ = _run(tmp_path, days=1)
    newest = max(e.ts for c in w.cases.values() for e in c.events)
    assert newest.date() == w.current_date.date()


def test_two_runs_with_the_same_seed_produce_the_same_world(tmp_path):
    a, _ = _run(tmp_path / "a", days=4)
    b, _ = _run(tmp_path / "b", days=4)
    assert a.to_dict() == b.to_dict()


class _AlwaysMoveRng:
    """A rng stub that never stalls and always picks the first candidate --
    so if `_drift` skips a case, that can only be because the case is in
    `touched`, never because chance happened to stall it."""

    def random(self) -> float:
        return 1.0  # never < any stall_prob in (0, 1], so never stalls

    def choice(self, seq):
        return seq[0]


def test_drift_only_touches_cases_nothing_else_moved(tmp_path):
    """`_drift` must never move -- or otherwise add an event to -- a case
    that's already in `touched`, and the returned moved-list must exclude
    every member of `touched`.

    Unit-tests `_drift` directly with an explicit `touched` set rather than
    inferring the invariant from an aggregate same-day event count on a full
    `advance()` run: `intents.choose()` can legitimately pick the same live
    case twice in one day, which produces a per-case-per-day count of 2 with
    no bug present at all -- a real skip-logic failure (drift moving an
    already-touched case) also lands on 2, so the count alone cannot tell
    the two apart. A rigged rng that would force every untouched, eligible
    case to move is used so the negative result (touched case unchanged)
    cannot be attributed to a stall draw instead of the `touched` check.
    """
    w = day0_from_generator("advisory")
    cfg = profile_config("advisory")

    live = [cid for cid, c in w.cases.items()
            if c.stage not in cfg["terminal_stages"]
            and worker_mod.next_stage(w, c, cfg) is not None]
    assert len(live) >= 2, "fixture needs at least two advanceable live cases"
    touched_cid, free_cid = live[0], live[1]
    before = {cid: len(c.events) for cid, c in w.cases.items()}

    moved = step_mod._drift(w, cfg, _AlwaysMoveRng(), {touched_cid})

    # The touched case must be skipped entirely: no new event, never in moved.
    assert touched_cid not in moved
    assert len(w.cases[touched_cid].events) == before[touched_cid]

    # Control: an untouched, eligible case DOES move under this rng, proving
    # the rng was actually capable of producing a move (so the assertion
    # above is not vacuously true).
    assert free_cid in moved
    assert len(w.cases[free_cid].events) == before[free_cid] + 1


def test_advance_touched_set_reflects_approval_outcome(tmp_path, monkeypatch):
    """advance()'s construction of `touched` from worker.apply_approved's
    effect records (step.py:80-84) must:

      (a) include a case whose effect record has outcome == "applied", so
          that case is excluded from that day's drift;
      (b) NOT include a case whose effect record has outcome == "failed",
          so that case remains eligible for drift exactly as if no approval
          had been made -- a failed approval must never silently suppress a
          case's drift, which would let an approval that DID NOT WORK still
          change the world through a channel the outcome measurement can't
          see;
      (c) NOT include anything from a record with case_id None at all
          (process-intervention and machine-executable-template-skip
          records both carry case_id: None) -- neither in `touched` nor in
          `row_changes`.

    worker.apply_approved's probability draw is not the unit under test
    here (that's advance()'s own filter, step.py:80-84), so apply_approved
    is monkeypatched to a fixed record list rather than hunting for a
    discriminating seed, per the coordinator's guidance -- this is the same
    "test the unit, not its stochastic upstream" reasoning as the rigged rng
    in test_drift_only_touches_cases_nothing_else_moved above.
    intents.choose is also monkeypatched to produce no messages, so the day
    has exactly one source of touched cases (the effect records) and there
    is no chance a real message coincidentally touches the same case,
    which would make the (b)/(c) assertions unreliable.

    `_drift` is spied on, not replaced, so its real (already separately
    proven) behaviour still runs -- this test only captures the `touched`
    argument advance() hands it.
    """
    w = day0_from_generator("advisory")
    cfg = profile_config("advisory")
    live = [cid for cid, c in w.cases.items()
            if c.stage not in cfg["terminal_stages"]]
    assert len(live) >= 2, "fixture needs at least two live cases"
    applied_cid, failed_cid = live[0], live[1]

    fixed_effects = [
        {"action_id": "A1", "finding_type": "stalled_case",
         "case_id": applied_cid, "outcome": "applied"},
        {"action_id": "A2", "finding_type": "stalled_case",
         "case_id": failed_cid, "outcome": "failed"},
        {"action_id": "A3", "finding_type": "delay",
         "case_id": None, "outcome": "applied"},
        {"action_id": "A4", "finding_type": "normalise_status_values",
         "case_id": None, "outcome": "unwired"},
    ]
    monkeypatch.setattr(step_mod.worker_mod, "apply_approved",
                        lambda world, items, rng, cfg: fixed_effects)
    monkeypatch.setattr(step_mod.intents_mod, "choose",
                        lambda world, rng, cfg: [])

    captured = {}
    real_drift = step_mod._drift

    def _spy_drift(world, cfg, rng, touched):
        captured["touched"] = set(touched)
        return real_drift(world, cfg, rng, touched)

    monkeypatch.setattr(step_mod, "_drift", _spy_drift)

    result = advance(w, [], drive_dir=tmp_path / "drive",
                     cache_dir=tmp_path / "cache", use_llm=False)

    # (a) applied + real case_id -> in touched, in row_changes.
    assert applied_cid in captured["touched"]
    assert applied_cid in result.row_changes

    # (b) failed + real case_id -> excluded from both.
    assert failed_cid not in captured["touched"]
    assert failed_cid not in result.row_changes

    # (c) case_id None (regardless of outcome) -> never leaks in.
    assert None not in captured["touched"]
    assert None not in result.row_changes


def test_day_result_serialises(tmp_path):
    _, results = _run(tmp_path, days=1)
    d = results[0].to_dict()
    assert set(d) >= {"day", "date", "messages", "row_changes", "effects",
                      "files"}


# ── F1: a multi-day --advance must not re-apply an already-applied approval ─
# simulator/cli.py's `--advance N` is the documented default workflow
# (HANDOVER.md) and calls advance() once per day with the SAME approved-items
# list, since nothing writes back to the action store. Measured on the real
# advisory world before this fix: one approved stalled_case walked a single
# case through 4 stage advances in 5 days; one approved delay process
# intervention shifted stall_prob.Proposal on 5 of 6 days.

def test_repeated_advance_does_not_reapply_an_approved_effect(tmp_path):
    w = day0_from_generator("advisory")
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    per_case_item = ActionItem(
        action_id="A-unowned", profile="advisory",
        finding_key="unowned_case::x::y", finding_type="unowned_case",
        title="t", summary="s", workflow="Lead-to-cash", stage="Lead",
        affected_case_ids=[cid], status="approved",
        created_at="2026-07-20", updated_at="2026-07-20")
    process_item = ActionItem(
        action_id="A-delay", profile="advisory",
        finding_key="delay::x::y", finding_type="delay",
        title="t", summary="s", workflow="Lead-to-cash", stage="Proposal",
        affected_case_ids=[], status="approved",
        action_category="process_intervention",
        created_at="2026-07-20", updated_at="2026-07-20")

    before_param = w.params["stall_prob.Proposal"]
    for _ in range(5):
        advance(w, [per_case_item, process_item], drive_dir=tmp_path / "drive",
               cache_dir=tmp_path / "cache", use_llm=False)

    applied_case_effects = [e for e in w.intent["effects"]
                            if e["action_id"] == "A-unowned"
                            and e["outcome"] == "applied"]
    applied_process_effects = [e for e in w.intent["effects"]
                               if e["action_id"] == "A-delay"
                               and e["outcome"] == "applied"]
    # Never more than one success recorded for either item, across the
    # whole 5-day window -- the invariant under test.
    assert len(applied_case_effects) <= 1
    assert len(applied_process_effects) <= 1
    # unowned_case's effect_prob is 0.95, so across 5 days a success is
    # virtually certain -- non-emptiness makes the <=1 assertion above
    # non-vacuous rather than trivially true on zero applications.
    assert applied_case_effects, (
        "expected at least one success across 5 days at effect_prob=0.95")
    if applied_process_effects:
        delta = CFG["process_param_delta"]["delay"]["stall_prob.Proposal"]
        expected = max(CFG["param_floor"], before_param + delta)
        assert w.params["stall_prob.Proposal"] == expected


# ── F6: repetition_prob / rework_prob are wired into _drift ─────────────────
# Before this fix these two parameters were written by apply_approved's
# process-param branch but read by nothing -- an approved repetition/rework
# process intervention changed nothing, and the simulator could never
# generate either pattern organically (only delay). These tests prove the
# mechanism exists and that lowering the parameter genuinely reduces it.

def _stalled_world():
    """Every case guaranteed to stall today (stall_prob forced to 1.0 for
    every stage), so repetition/rework's own draws are the only source of
    randomness left."""
    w = day0_from_generator("advisory")
    for k in list(w.params):
        if k.startswith("stall_prob."):
            w.params[k] = 1.0
    return w


def test_drift_can_repeat_the_current_stage():
    w = _stalled_world()
    w.params["repetition_prob"] = 1.0
    w.params["rework_prob"] = 0.0
    live_before = {cid: (c.stage, len(c.events))
                   for cid, c in w.cases.items()
                   if c.stage not in CFG["terminal_stages"]}
    assert live_before, "fixture needs at least one live case"

    moved = step_mod._drift(w, CFG, random.Random(1), set())

    for cid, (stage, n) in live_before.items():
        assert cid in moved
        assert w.cases[cid].stage == stage, "repetition must NOT change stage"
        assert len(w.cases[cid].events) == n + 1, (
            "repetition must add exactly one duplicate entry")


def test_drift_can_rework_back_one_stage():
    w = _stalled_world()
    w.params["repetition_prob"] = 0.0
    w.params["rework_prob"] = 1.0
    order = CFG["stage_order"]
    live_before = {cid: c.stage for cid, c in w.cases.items()
                   if c.stage not in CFG["terminal_stages"]
                   and order.index(c.stage) > 0}
    assert live_before, "fixture needs a live case with a previous stage"

    moved = step_mod._drift(w, CFG, random.Random(1), set())

    for cid, stage in live_before.items():
        assert cid in moved
        assert w.cases[cid].stage == order[order.index(stage) - 1], (
            "rework must step back exactly one stage")


def test_repetition_and_rework_are_mutually_exclusive_on_the_same_case():
    """Both probabilities at 1.0: repetition is checked first in _drift, so
    it must win -- a case must never get both a duplicate entry AND a
    backward step on the same day."""
    w = _stalled_world()
    w.params["repetition_prob"] = 1.0
    w.params["rework_prob"] = 1.0
    cid = next(cid for cid, c in w.cases.items()
              if c.stage not in CFG["terminal_stages"])
    before_stage = w.cases[cid].stage
    before_n = len(w.cases[cid].events)

    step_mod._drift(w, CFG, random.Random(1), set())

    assert w.cases[cid].stage == before_stage
    assert len(w.cases[cid].events) == before_n + 1


def test_lower_repetition_prob_reduces_repetition_incidence():
    def _incidence(rep_prob, trials=40):
        hits = 0
        for s in range(trials):
            w = _stalled_world()
            w.params["repetition_prob"] = rep_prob
            w.params["rework_prob"] = 0.0
            cid = next(cid for cid, c in w.cases.items()
                      if c.stage not in CFG["terminal_stages"])
            before_n = len(w.cases[cid].events)
            step_mod._drift(w, CFG, random.Random(s), set())
            if len(w.cases[cid].events) > before_n:
                hits += 1
        return hits

    high = _incidence(0.9)
    low = _incidence(0.05)
    assert high > low, (
        f"an approved intervention lowering repetition_prob must reduce "
        f"incidence: high={high} low={low}")


def test_lower_rework_prob_reduces_rework_incidence():
    def _incidence(rework_prob, trials=40):
        hits = 0
        order = CFG["stage_order"]
        for s in range(trials):
            w = _stalled_world()
            w.params["repetition_prob"] = 0.0
            w.params["rework_prob"] = rework_prob
            cid = next(cid for cid, c in w.cases.items()
                      if c.stage not in CFG["terminal_stages"]
                      and order.index(c.stage) > 0)
            before_stage = w.cases[cid].stage
            step_mod._drift(w, CFG, random.Random(s), set())
            if w.cases[cid].stage != before_stage:
                hits += 1
        return hits

    high = _incidence(0.9)
    low = _incidence(0.05)
    assert high > low, (
        f"an approved intervention lowering rework_prob must reduce "
        f"incidence: high={high} low={low}")
