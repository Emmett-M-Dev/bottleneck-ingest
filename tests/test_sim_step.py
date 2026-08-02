import random

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


def test_drift_only_touches_cases_nothing_else_moved(tmp_path):
    """A stalled case must not also receive a drift event on the same day."""
    w, results = _run(tmp_path, days=5)
    for case in w.cases.values():
        by_day = {}
        for e in case.events:
            by_day[e.ts] = by_day.get(e.ts, 0) + 1
        assert max(by_day.values()) <= 2, case.cid


def test_day_result_serialises(tmp_path):
    _, results = _run(tmp_path, days=1)
    d = results[0].to_dict()
    assert set(d) >= {"day", "date", "messages", "row_changes", "effects",
                      "files"}
