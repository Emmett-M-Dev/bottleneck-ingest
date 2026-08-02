import random

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
