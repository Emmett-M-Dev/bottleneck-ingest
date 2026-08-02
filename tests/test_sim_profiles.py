import pytest

import config
from simulator.profiles import SIM_PROFILES, profile_config


def test_paths_are_under_data_sim():
    assert config.sim_dir("advisory").name == "advisory"
    assert config.sim_dir("advisory").parent == config.DATA_SIM
    assert config.sim_drive_dir("advisory").name == "drive"
    assert config.sim_state_path("advisory").name == "state.json"
    assert config.sim_inbox_path("advisory").name == "inbox.jsonl"


def test_advisory_profile_is_wired():
    cfg = profile_config("advisory")
    for field in ("generator", "stage_order", "first_stage", "terminal_stages",
                  "arrival_rate_per_day", "params", "effect_prob",
                  "process_param_delta", "process_effect_prob", "personas",
                  "intents"):
        assert field in cfg, f"missing {field}"


def test_every_wired_finding_type_has_a_probability():
    wired = {"stage_sla_breach", "stalled_case", "unowned_case",
             "unrealised_value", "overloaded_owner", "key_person_dependency"}
    assert set(profile_config("advisory")["effect_prob"]) == wired


def test_every_process_delta_has_an_effect_probability():
    cfg = profile_config("advisory")
    assert set(cfg["process_effect_prob"]) == set(cfg["process_param_delta"])


def test_unknown_profile_names_the_known_ones():
    with pytest.raises(KeyError, match="advisory"):
        profile_config("nope")


def test_only_advisory_is_wired_for_now():
    assert set(SIM_PROFILES) == {"advisory"}
