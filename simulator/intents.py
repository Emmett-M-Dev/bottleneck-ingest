"""What arrives in the SME's inbox on a given day.

Generic: an intent knows whether it needs a live case and who tends to send it,
never which SME it belongs to. Weights come from the profile (`cfg["intents"]`).

CATALOGUE's `persona` field is descriptive metadata only — nothing in
simulator/ reads it, so it is deliberately fixed here rather than sourced
from a profile key: a persona label ("client contact", "subcontractor", ...)
that nothing consumes is not a real per-SME knob, and giving it one would
just move the same decoration into config without making it do anything
(F3 review; simulator/profiles.py used to carry a dead `personas` list on
exactly this premise — removed rather than wired, for the same reason).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from simulator.world import WorldState


@dataclass(frozen=True)
class Intent:
    id: str
    needs_case: bool
    persona: str


CATALOGUE: dict[str, Intent] = {
    "new_enquiry":     Intent("new_enquiry", False, "prospect"),
    "progress_update": Intent("progress_update", True, "client"),
    "client_query":    Intent("client_query", True, "client"),
    "payment_made":    Intent("payment_made", True, "client"),
    "scope_change":    Intent("scope_change", True, "client"),
}


def _live_case_ids(world: WorldState, cfg: dict) -> list[str]:
    terminal = {s.strip().lower() for s in cfg["terminal_stages"]}
    return sorted(cid for cid, c in world.cases.items()
                  if c.stage.strip().lower() not in terminal)


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth's method — small means only, which is all an SME day needs."""
    import math
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def choose(world: WorldState, rng: random.Random, cfg: dict
           ) -> list[tuple[Intent, str | None]]:
    """The day's inbound traffic: (intent, case_id) pairs."""
    live = _live_case_ids(world, cfg)
    weights = cfg["intents"]
    ids = sorted(weights)

    out: list[tuple[Intent, str | None]] = []

    for _ in range(_poisson(rng, cfg["arrival_rate_per_day"])):
        out.append((CATALOGUE["new_enquiry"], None))

    # Roughly one touch per twelve live cases per day, floor of zero.
    # (Poisson with zero mean never fires, so no case-bound traffic when live is empty.)
    for _ in range(_poisson(rng, len(live) / 12.0)):
        pool = [i for i in ids if CATALOGUE[i].needs_case]
        pool_weights = [weights[i] for i in pool]
        if not pool or sum(pool_weights) == 0:
            # No positively-weighted case-bound intents; no case-bound traffic this iteration.
            continue
        pick = rng.choices(pool, weights=pool_weights, k=1)[0]
        out.append((CATALOGUE[pick], rng.choice(live)))

    return out
