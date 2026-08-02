from simulator.intents import CATALOGUE, choose
from simulator.profiles import profile_config
from simulator.world import day0_from_generator


def test_catalogue_covers_the_configured_intents():
    assert set(CATALOGUE) >= set(profile_config("advisory")["intents"])


def test_only_new_enquiry_may_have_no_case():
    for intent in CATALOGUE.values():
        assert intent.needs_case == (intent.id != "new_enquiry")


def test_choose_is_deterministic_for_a_seed():
    w = day0_from_generator("advisory")
    cfg = profile_config("advisory")
    a = choose(w, w.rng_for_day(1), cfg)
    b = choose(w, w.rng_for_day(1), cfg)
    assert [(i.id, c) for i, c in a] == [(i.id, c) for i, c in b]


def test_case_bound_intents_target_a_live_case():
    w = day0_from_generator("advisory")
    cfg = profile_config("advisory")
    terminal = {s.lower() for s in cfg["terminal_stages"]}
    for intent, cid in choose(w, w.rng_for_day(3), cfg):
        if intent.needs_case:
            assert cid in w.cases
            assert w.cases[cid].stage.lower() not in terminal
        else:
            assert cid is None
