import random

from actions.models import ActionItem
from simulator.compose import Message
from simulator.profiles import profile_config
from simulator.worker import (WIRED_FINDING_TYPES, apply_approved,
                              apply_message, next_stage)
from simulator.world import day0_from_generator

CFG = profile_config("advisory")


def _world():
    return day0_from_generator("advisory")


def _item(finding_type, case_ids, *, status="approved",
          template=None) -> ActionItem:
    return ActionItem(
        action_id=f"A-{finding_type}", profile="advisory",
        finding_key=f"{finding_type}::x::y", finding_type=finding_type,
        title="t", summary="s", workflow="Lead-to-cash", stage="Lead",
        affected_case_ids=list(case_ids), status=status,
        action_template=template, created_at="2026-07-20",
        updated_at="2026-07-20")


def _msg(intent, case_id):
    return Message(msg_id="M001-00", day=1, sent_at="", sender="X",
                   subject="s", body="b", intent=intent, case_id=case_id)


def test_wired_set_matches_the_profile_probabilities():
    assert WIRED_FINDING_TYPES == frozenset(CFG["effect_prob"])


def test_progress_update_advances_the_case_one_stage():
    w = _world()
    cid = next(c for c, k in w.cases.items()
               if k.stage not in CFG["terminal_stages"])
    before = w.cases[cid].stage
    expected = next_stage(w, w.cases[cid], CFG)
    ref = apply_message(w, _msg("progress_update", cid), random.Random(1), CFG)
    assert w.cases[cid].stage == expected != before
    assert ref and ref.endswith(cid)


def test_client_query_changes_status_but_not_stage():
    w = _world()
    cid = next(iter(w.cases))
    before = w.cases[cid].stage
    n_before = len(w.cases[cid].events)
    apply_message(w, _msg("client_query", cid), random.Random(1), CFG)
    assert w.cases[cid].stage == before
    assert len(w.cases[cid].events) == n_before + 1


def test_new_enquiry_creates_a_case_at_the_first_stage():
    w = _world()
    n = len(w.cases)
    apply_message(w, _msg("new_enquiry", None), random.Random(1), CFG)
    assert len(w.cases) == n + 1
    newest = max(w.cases.values(), key=lambda c: c.last_ts)
    assert newest.stage == CFG["first_stage"]


def test_approved_unowned_case_gets_an_owner():
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    out = apply_approved(w, [_item("unowned_case", [cid])],
                         random.Random(1), CFG)
    assert out[0]["outcome"] == "applied"
    assert w.cases[cid].owner != ""


def test_unapproved_items_are_ignored():
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    out = apply_approved(w, [_item("unowned_case", [cid], status="proposed")],
                         random.Random(1), CFG)
    assert out == []
    assert w.cases[cid].owner == ""


def test_unwired_finding_types_are_recorded_not_acted_on():
    w = _world()
    cid = next(iter(w.cases))
    n_before = len(w.cases[cid].events)
    out = apply_approved(w, [_item("delay", [cid])], random.Random(1), CFG)
    assert out and out[0]["outcome"] == "unwired"
    assert len(w.cases[cid].events) == n_before


def test_machine_executable_template_is_never_touched():
    """normalise_status_values belongs to the remediation executor."""
    w = _world()
    cid = next(iter(w.cases))
    n_before = len(w.cases[cid].events)
    out = apply_approved(
        w, [_item("messy_status", [cid], template="normalise_status_values")],
        random.Random(1), CFG)
    assert all(o["outcome"] != "applied" for o in out)
    assert len(w.cases[cid].events) == n_before


def test_effects_are_probabilistic_not_guaranteed():
    """Over many draws at p=0.5 both outcomes must occur, or the simulator is
    authored to flatter the product."""
    outcomes = set()
    for s in range(40):
        w = _world()
        cid = next(c for c, k in w.cases.items()
                   if k.stage not in CFG["terminal_stages"])
        out = apply_approved(w, [_item("unrealised_value", [cid])],
                             random.Random(s), CFG)
        outcomes.add(out[0]["outcome"])
    assert outcomes == {"applied", "failed"}
