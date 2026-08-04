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
          template=None, category="case_action", action_id=None) -> ActionItem:
    return ActionItem(
        action_id=action_id or f"A-{finding_type}", profile="advisory",
        finding_key=f"{finding_type}::x::y", finding_type=finding_type,
        title="t", summary="s", workflow="Lead-to-cash", stage="Lead",
        affected_case_ids=list(case_ids), status=status,
        action_template=template, action_category=category,
        created_at="2026-07-20", updated_at="2026-07-20")


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


# Fix round 1: process_effect_prob["delay"] = 0.60. random.Random(1).random()
# == 0.1343... (< 0.60, succeeds); random.Random(0).random() == 0.8444...
# (>= 0.60, fails). Deterministic seeds, not a statistical loop, per review.
_DELAY_APPLIED_SEED = 1
_DELAY_FAILED_SEED = 0


def test_approved_process_intervention_shifts_the_param_on_success():
    w = _world()
    before = w.params["stall_prob.Proposal"]
    out = apply_approved(
        w, [_item("delay", [], category="process_intervention")],
        random.Random(_DELAY_APPLIED_SEED), CFG)
    delta = CFG["process_param_delta"]["delay"]["stall_prob.Proposal"]
    assert out[0]["outcome"] == "applied"
    assert w.params["stall_prob.Proposal"] == before + delta


def test_approved_process_intervention_leaves_the_param_alone_on_failure():
    w = _world()
    before = w.params["stall_prob.Proposal"]
    out = apply_approved(
        w, [_item("delay", [], category="process_intervention")],
        random.Random(_DELAY_FAILED_SEED), CFG)
    assert out[0]["outcome"] == "failed"
    assert w.params["stall_prob.Proposal"] == before


def test_repeated_process_interventions_clamp_at_the_param_floor():
    """FIVE SEPARATE approved items (distinct action_ids -- five different
    process-intervention decisions taken over time), not the same item
    re-supplied -- since F1's fix makes a single action_id shift its
    parameter at most once. Clamping at the floor is still a property of
    the param_floor logic itself, exercised here across several approvals."""
    w = _world()
    for i in range(5):
        item = _item("delay", [], category="process_intervention",
                     action_id=f"A-delay-{i}")
        out = apply_approved(w, [item], random.Random(_DELAY_APPLIED_SEED), CFG)
        assert out[0]["outcome"] == "applied"
    assert w.params["stall_prob.Proposal"] == CFG["param_floor"]
    assert w.params["stall_prob.Proposal"] >= CFG["param_floor"]


# ── F1: apply_approved must be idempotent for a still-`approved` item ───────
# simulator/cli.py's `--advance N` (the documented default workflow) passes
# the SAME approved-items list to advance()/apply_approved once per day, and
# nothing here writes back to the action store -- `status` stays "approved"
# for as long as it is. Measured on the real advisory world before this fix:
# one approved stalled_case walked a single case through 4 stage advances in
# 5 days; one approved delay process intervention shifted stall_prob.Proposal
# on 5 of 6 days. These tests pin the fix directly against worker.py, the
# module the finding names.

def test_repeated_apply_approved_calls_only_apply_a_wired_effect_once():
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    item = _item("unowned_case", [cid])

    out1 = apply_approved(w, [item], random.Random(1), CFG)
    assert out1 and out1[0]["outcome"] == "applied"
    owner_after_first = w.cases[cid].owner
    n_events_after_first = len(w.cases[cid].events)
    assert owner_after_first != ""

    # Same item, still "approved" (as it would be on day 2 of a multi-day
    # --advance, since nothing here ever flips status): must be a complete
    # no-op -- no new record, no new event, no owner change.
    out2 = apply_approved(w, [item], random.Random(1), CFG)
    assert out2 == []
    assert w.cases[cid].owner == owner_after_first
    assert len(w.cases[cid].events) == n_events_after_first


def test_repeated_process_intervention_calls_shift_the_param_only_once():
    w = _world()
    before = w.params["stall_prob.Proposal"]
    item = _item("delay", [], category="process_intervention")

    out1 = apply_approved(w, [item], random.Random(_DELAY_APPLIED_SEED), CFG)
    assert out1[0]["outcome"] == "applied"
    delta = CFG["process_param_delta"]["delay"]["stall_prob.Proposal"]
    after_first = before + delta
    assert w.params["stall_prob.Proposal"] == after_first

    out2 = apply_approved(w, [item], random.Random(_DELAY_APPLIED_SEED), CFG)
    assert out2 == []
    assert w.params["stall_prob.Proposal"] == after_first


def test_a_failed_draw_is_retried_on_a_later_call_not_deduped():
    """The dedup guard must only remember SUCCESS -- a case/item that has
    not yet succeeded must still be retried, exactly as before F1.
    unowned_case's effect_prob is 0.95; random.Random(2).random() ==
    0.9560... (>= 0.95, fails), random.Random(1).random() == 0.1343...
    (< 0.95, succeeds). Deterministic seeds, not a statistical loop, per
    the fix-round-1 convention this file already follows."""
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner == "")
    item = _item("unowned_case", [cid])

    out1 = apply_approved(w, [item], random.Random(2), CFG)
    assert out1 and out1[0]["outcome"] == "failed"
    assert w.cases[cid].owner == ""

    out2 = apply_approved(w, [item], random.Random(1), CFG)
    assert out2 and out2[0]["outcome"] == "applied"
    assert w.cases[cid].owner != ""


# ── F5: a no-op effect must never be reported as "applied" ──────────────────
# `unrealised_value` on an already-terminal case, and `unowned_case` on an
# already-owned case, add zero events -- crediting either with "applied" is
# exactly the confound tests/test_sim_e2e.py exists to exclude.

def test_unrealised_value_on_an_already_terminal_case_is_not_credited():
    w = _world()
    cid = next(iter(w.cases))
    terminal = CFG["terminal_stages"][-1]
    last = w.cases[cid].events[-1]
    w.cases[cid].add(terminal, last.ts, w.cases[cid].owner, "done")
    n_before = len(w.cases[cid].events)

    out = apply_approved(w, [_item("unrealised_value", [cid])],
                         random.Random(1), CFG)
    assert out[0]["outcome"] != "applied"
    assert len(w.cases[cid].events) == n_before


def test_unowned_case_that_already_has_an_owner_is_not_credited():
    w = _world()
    cid = next(c for c, k in w.cases.items() if k.owner != "")
    n_before = len(w.cases[cid].events)

    out = apply_approved(w, [_item("unowned_case", [cid])],
                         random.Random(1), CFG)
    assert out[0]["outcome"] != "applied"
    assert len(w.cases[cid].events) == n_before
