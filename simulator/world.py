"""The world the simulator advances.

A WorldState is a bag of cases; a case owns an append-only list of events.
Stages are stored CANONICAL here — the messy surface spelling that staff
actually type is applied by simulator/render.py, so world logic never has to
parse mess it created itself.
"""

from __future__ import annotations

import importlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from simulator.profiles import profile_config


def canon_stage(label: str, stage_order: list[str]) -> str:
    """Map a possibly-messy stage label onto the profile's canonical spelling.
    Unknown labels pass through unchanged rather than being dropped."""
    lookup = {s.strip().lower(): s for s in stage_order}
    return lookup.get(str(label).strip().lower(), str(label).strip())


@dataclass
class SimEvent:
    stage: str
    ts: datetime
    actor: str
    status: str

    def to_dict(self) -> dict:
        return {"stage": self.stage, "ts": self.ts.isoformat(),
                "actor": self.actor, "status": self.status}

    @staticmethod
    def from_dict(d: dict) -> "SimEvent":
        return SimEvent(stage=d["stage"], ts=datetime.fromisoformat(d["ts"]),
                        actor=d["actor"], status=d["status"])


@dataclass
class SimCase:
    """Case-level field names (cid, client, value) duck-type
    generate_messy_advisory.Engagement so the case object can be passed where
    an Engagement is expected. However, events holds SimEvent dataclasses with
    canonical stage names, not the dict shape with 'activity' keys that
    generator builders expect. Any generator builder that subscripts events
    requires an adapter supplying the dict form."""
    cid: str
    client: str
    value: float
    events: list[SimEvent] = field(default_factory=list)

    @property
    def last_ts(self) -> datetime:
        return self.events[-1].ts

    @property
    def stage(self) -> str:
        return self.events[-1].stage

    @property
    def owner(self) -> str:
        return self.events[-1].actor or ""

    def add(self, stage: str, ts: datetime, actor: str, status: str) -> None:
        self.events.append(SimEvent(stage=stage, ts=ts, actor=actor,
                                    status=status))

    def to_dict(self) -> dict:
        return {"cid": self.cid, "client": self.client, "value": self.value,
                "events": [e.to_dict() for e in self.events]}

    @staticmethod
    def from_dict(d: dict) -> "SimCase":
        return SimCase(cid=d["cid"], client=d["client"], value=d["value"],
                       events=[SimEvent.from_dict(e) for e in d["events"]])


@dataclass
class WorldState:
    profile: str
    seed: int
    day: int
    start_date: datetime
    cases: dict[str, SimCase]
    params: dict[str, float]
    next_case_num: int
    intent: dict

    @property
    def current_date(self) -> datetime:
        return self.start_date + timedelta(days=self.day)

    def rng_for_day(self, day: int) -> random.Random:
        """One generator per (seed, day). Same seed + same approvals => the
        same world, which is what makes the eval replay reproducible."""
        return random.Random(f"{self.seed}|{day}")

    def to_dict(self) -> dict:
        return {
            "profile": self.profile, "seed": self.seed, "day": self.day,
            "start_date": self.start_date.isoformat(),
            "cases": {k: v.to_dict() for k, v in self.cases.items()},
            "params": dict(self.params), "next_case_num": self.next_case_num,
            "intent": dict(self.intent),
        }


def from_dict(d: dict) -> WorldState:
    return WorldState(
        profile=d["profile"], seed=d["seed"], day=d["day"],
        start_date=datetime.fromisoformat(d["start_date"]),
        cases={k: SimCase.from_dict(v) for k, v in d["cases"].items()},
        params=dict(d["params"]), next_case_num=d["next_case_num"],
        intent=dict(d["intent"]),
    )


def day0_from_generator(profile: str) -> WorldState:
    """Import the existing hand-authored world as day 0.

    The generator uses the MODULE-LEVEL random, so it is seeded here before
    build_events() is called. This is the one place in simulator/ that touches
    module-level random; everything downstream uses WorldState.rng_for_day.
    """
    cfg = profile_config(profile)
    mod = importlib.import_module(cfg["generator"])
    random.seed(mod.SEED)
    engagements, structural, operational = mod.build_events()

    order = cfg["stage_order"]
    cases: dict[str, SimCase] = {}
    for eng in engagements:
        cases[eng.cid] = SimCase(
            cid=eng.cid, client=eng.client, value=float(eng.value),
            events=[SimEvent(stage=canon_stage(e["activity"], order),
                             ts=e["ts"], actor=e["actor"] or "",
                             status=e["status"]) for e in eng.events])

    return WorldState(
        profile=profile, seed=mod.SEED, day=0, start_date=mod.AS_OF,
        cases=cases, params=dict(cfg["params"]),
        next_case_num=1041 + len(engagements),
        intent={"stage_order": list(order), "structural": structural,
                "operational": operational, "arrivals": {}, "effects": []},
    )
