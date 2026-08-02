from datetime import datetime

from simulator.world import (SimCase, SimEvent, WorldState, canon_stage,
                             day0_from_generator, from_dict)


def _case():
    return SimCase(cid="NA-1", client="Rowan Group", value=1000.0, events=[
        SimEvent(stage="Lead", ts=datetime(2026, 1, 1), actor="R", status="done"),
        SimEvent(stage="Proposal", ts=datetime(2026, 1, 9), actor="", status="tbc"),
    ])


def test_canon_stage_matches_ignoring_case_and_whitespace():
    order = ["Lead", "Client Review", "Paid"]
    assert canon_stage("  client review ", order) == "Client Review"
    assert canon_stage("LEAD", order) == "Lead"
    assert canon_stage("Mystery", order) == "Mystery"     # unknown passes through


def test_case_exposes_stage_owner_and_last_ts():
    c = _case()
    assert c.stage == "Proposal"
    assert c.owner == ""
    assert c.last_ts == datetime(2026, 1, 9)


def test_world_round_trips_through_dict():
    w = WorldState(profile="advisory", seed=7, day=3,
                   start_date=datetime(2026, 7, 20), cases={"NA-1": _case()},
                   params={"stall_prob.Lead": 0.5}, next_case_num=1042,
                   intent={"arrivals": {}})
    back = from_dict(w.to_dict())
    assert back.day == 3
    assert back.current_date == datetime(2026, 7, 23)
    assert back.cases["NA-1"].stage == "Proposal"
    assert back.params == {"stall_prob.Lead": 0.5}


def test_rng_is_deterministic_per_day_and_differs_across_days():
    w = WorldState(profile="advisory", seed=7, day=0,
                   start_date=datetime(2026, 7, 20), cases={}, params={},
                   next_case_num=1, intent={})
    assert w.rng_for_day(4).random() == w.rng_for_day(4).random()
    assert w.rng_for_day(4).random() != w.rng_for_day(5).random()


def test_day0_import_loads_the_advisory_world():
    w = day0_from_generator("advisory")
    assert w.day == 0
    assert len(w.cases) == 27              # len(_PLAN) in the generator
    assert w.next_case_num == 1041 + 27    # ids run NA-1041 .. NA-1067
    # every event stage canonicalised against the profile's stage order
    order = set(w.intent["stage_order"])
    stages = {e.stage for c in w.cases.values() for e in c.events}
    assert stages <= order, f"uncanonicalised stages: {stages - order}"
    # the generator's own ground truth is carried through as recorded INTENT
    assert w.intent["structural"]["delay"]
    assert w.intent["operational"]["parked_at"]


def test_day0_import_is_reproducible():
    a, b = day0_from_generator("advisory"), day0_from_generator("advisory")
    assert a.to_dict() == b.to_dict()


def test_datetime_round_trip_preserves_microseconds():
    ts_with_micros = datetime(2026, 1, 1, 12, 30, 45, 123456)
    event = SimEvent(stage="Lead", ts=ts_with_micros, actor="A", status="done")
    back = SimEvent.from_dict(event.to_dict())
    assert back.ts == ts_with_micros
    assert back.ts.microsecond == 123456


def test_to_dict_returns_independent_copies_of_params_and_intent():
    w = WorldState(profile="advisory", seed=7, day=0,
                   start_date=datetime(2026, 7, 20), cases={}, params={},
                   next_case_num=1, intent={})
    d = w.to_dict()
    # Mutate the original world's params and intent
    w.params["new_key"] = 0.5
    w.intent["new_field"] = "value"
    # Verify the dict returned by to_dict() was not affected
    assert "new_key" not in d["params"]
    assert "new_field" not in d["intent"]
