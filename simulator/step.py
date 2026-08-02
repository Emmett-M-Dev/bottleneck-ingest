"""One sim day, committed atomically.

    arrivals -> inbound -> worker(messages) -> worker(approved) -> drift
             -> render

The day is the unit of truth: world state and the spreadsheet write happen
once, at the boundary. A UI may drip the day's messages out on a timer, but
nothing downstream ever sees a half-applied day.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from simulator import compose as compose_mod
from simulator import intents as intents_mod
from simulator import render as render_mod
from simulator import worker as worker_mod
from simulator.profiles import profile_config
from simulator.world import WorldState


@dataclass
class DayResult:
    day: int
    date: str
    messages: list = field(default_factory=list)
    row_changes: list = field(default_factory=list)
    effects: list = field(default_factory=list)
    files: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"day": self.day, "date": self.date,
                "messages": [m.to_dict() for m in self.messages],
                "row_changes": list(self.row_changes),
                "effects": list(self.effects), "files": list(self.files)}


def _drift(world: WorldState, cfg: dict, rng: random.Random,
           touched: set[str]) -> list[str]:
    """Cases nobody touched today age, and sometimes move on their own."""
    moved = []
    for cid, case in list(world.cases.items()):
        if cid in touched:
            continue
        if case.stage in cfg["terminal_stages"]:
            continue
        stall = world.params.get(f"stall_prob.{case.stage}", 0.5)
        if rng.random() < stall:
            continue
        if worker_mod.advance_case(world, case, cfg, rng):
            moved.append(cid)
    return moved


def advance(world: WorldState, approved, *, drive_dir, cache_dir,
            use_llm: bool = False) -> DayResult:
    cfg = profile_config(world.profile)
    world.day += 1
    rng = world.rng_for_day(world.day)

    picks = intents_mod.choose(world, rng, cfg)
    messages = [
        compose_mod.compose(intent, day=world.day, seq=i,
                            case=world.cases.get(cid) if cid else None,
                            rng=rng, cache_dir=Path(cache_dir),
                            use_llm=use_llm, seed=world.seed,
                            start_date=world.start_date)
        for i, (intent, cid) in enumerate(picks)]

    row_changes, touched = [], set()
    for msg in messages:
        ref = worker_mod.apply_message(world, msg, rng, cfg)
        if ref:
            row_changes.append(ref)
            touched.add(ref)

    effects = worker_mod.apply_approved(world, list(approved), rng, cfg)
    touched.update(e["case_id"] for e in effects
                   if e["outcome"] == "applied" and e["case_id"])
    row_changes.extend(e["case_id"] for e in effects
                       if e["outcome"] == "applied" and e["case_id"])

    row_changes.extend(_drift(world, cfg, rng, touched))

    files = render_mod.render(world, Path(drive_dir), rng)

    return DayResult(day=world.day, date=world.current_date.date().isoformat(),
                     messages=messages, row_changes=row_changes,
                     effects=effects, files=files)
