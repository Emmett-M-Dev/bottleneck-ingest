"""The SME's staff.

Two jobs: type inbound messages into the spreadsheets, and act on the action
items a human approved. The second is the arrow that makes outcome measurement
possible — without it the world never responds to the product and affected-case
counts can only grow.

Effects are probabilistic. Some approved actions fail, because a simulator in
which every approved fix works is authored to flatter the product.
"""

from __future__ import annotations

import random

from actions.models import MACHINE_EXECUTABLE_TEMPLATES
from simulator.world import WorldState

# Finding types with a modelled worker effect. Everything else is inert and
# reported as "unwired", so coverage can be stated honestly in the write-up.
WIRED_FINDING_TYPES = frozenset({
    "stage_sla_breach", "stalled_case", "unowned_case",
    "unrealised_value", "overloaded_owner", "key_person_dependency",
})

_ACTIONABLE_STATUSES = {"approved", "assigned", "in_progress"}
_DONE = "done"
_OPEN = "with client"


def _people(world: WorldState) -> list[str]:
    seen = {e.actor for c in world.cases.values() for e in c.events if e.actor}
    return sorted(seen)


def next_stage(world: WorldState, case, cfg: dict) -> str | None:
    order = cfg["stage_order"]
    try:
        i = order.index(case.stage)
    except ValueError:
        return None
    return order[i + 1] if i + 1 < len(order) else None


def advance_case(world: WorldState, case, cfg: dict, rng: random.Random,
                 *, status: str = _DONE) -> str | None:
    """Move a case on one stage. Public — step.py's drift phase calls it."""
    nxt = next_stage(world, case, cfg)
    if nxt is None:
        return None
    actor = case.owner or rng.choice(_people(world) or [""])
    case.add(nxt, world.current_date, actor, status)
    return f"{case.cid}"


def apply_message(world: WorldState, msg, rng: random.Random,
                  cfg: dict) -> str | None:
    """Type one message into the sheets. Returns a row ref, or None if the
    message changed nothing."""
    ref = None

    if msg.intent == "new_enquiry":
        cid = cfg["case_id_fmt"].format(world.next_case_num)
        world.next_case_num += 1
        from simulator.world import SimCase
        case = SimCase(cid=cid, client=msg.sender, value=0.0)
        case.add(cfg["first_stage"], world.current_date,
                 rng.choice(_people(world) or [""]), _OPEN)
        world.cases[cid] = case
        world.intent.setdefault("arrivals", {})[cid] = world.day
        ref = cid

    elif msg.case_id and msg.case_id in world.cases:
        case = world.cases[msg.case_id]
        if msg.intent == "progress_update":
            ref = advance_case(world, case, cfg, rng)
        elif msg.intent == "payment_made":
            terminal = cfg["terminal_stages"][-1]
            if case.stage != terminal:
                case.add(terminal, world.current_date, case.owner or "", _DONE)
                ref = case.cid
        elif msg.intent == "scope_change":
            order = cfg["stage_order"]
            i = order.index(case.stage) if case.stage in order else 0
            if i > 0:
                case.add(order[i - 1], world.current_date,
                         case.owner or "", _OPEN)
                ref = case.cid
        elif msg.intent == "client_query":
            case.add(case.stage, world.current_date, case.owner or "", _OPEN)
            ref = case.cid

    msg.applied = ref is not None
    msg.row_ref = ref
    return ref


def _effect(world: WorldState, case, finding_type: str, cfg: dict,
            rng: random.Random) -> bool:
    if finding_type in ("stage_sla_breach", "stalled_case"):
        return advance_case(world, case, cfg, rng) is not None
    if finding_type == "unowned_case":
        if case.owner:
            return True
        case.add(case.stage, world.current_date,
                 rng.choice(_people(world) or ["Unassigned"]), _OPEN)
        return True
    if finding_type == "unrealised_value":
        terminal = cfg["terminal_stages"][-1]
        if case.stage == terminal:
            return True
        case.add(terminal, world.current_date, case.owner or "", _DONE)
        return True
    if finding_type in ("overloaded_owner", "key_person_dependency"):
        others = [p for p in _people(world) if p != case.owner]
        if not others:
            return False
        case.add(case.stage, world.current_date, rng.choice(others), _OPEN)
        return True
    return False


def apply_approved(world: WorldState, items, rng: random.Random,
                   cfg: dict) -> list[dict]:
    """Act on approved action items. Returns one record per item considered."""
    out: list[dict] = []
    for item in items:
        if item.status not in _ACTIONABLE_STATUSES:
            continue
        if item.action_template in MACHINE_EXECUTABLE_TEMPLATES:
            # Owned by the remediation executor (CLAUDE.md §4a). Never here.
            out.append({"action_id": item.action_id,
                        "finding_type": item.finding_type,
                        "case_id": None, "outcome": "unwired"})
            continue

        if item.finding_type not in WIRED_FINDING_TYPES:
            # process_param_delta only applies to a genuine process
            # intervention (CLAUDE.md §4a: "change an approval rule,
            # rebalance capacity..."). A case_action on a structural finding
            # type that happens to share a key (e.g. "delay") must still
            # report unwired — it is not a modelled effect here.
            delta = (cfg["process_param_delta"].get(item.finding_type)
                     if item.action_category == "process_intervention"
                     else None)
            if delta:
                floor = cfg["param_floor"]
                for key, d in delta.items():
                    world.params[key] = max(floor, world.params.get(key, 0) + d)
                outcome = "applied"
            else:
                outcome = "unwired"
            out.append({"action_id": item.action_id,
                        "finding_type": item.finding_type,
                        "case_id": None, "outcome": outcome})
            continue

        p = cfg["effect_prob"][item.finding_type]
        for cid in item.affected_case_ids:
            case = world.cases.get(cid)
            if case is None:
                continue
            ok = rng.random() < p and _effect(world, case, item.finding_type,
                                              cfg, rng)
            out.append({"action_id": item.action_id,
                        "finding_type": item.finding_type,
                        "case_id": cid,
                        "outcome": "applied" if ok else "failed"})
    world.intent.setdefault("effects", []).extend(out)
    return out
