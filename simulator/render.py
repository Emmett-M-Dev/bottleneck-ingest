"""Project the world onto the SME's messy spreadsheets.

Every write is tmp-then-os.replace, so the product can be mid-ingest while a
tick renders. Mess (stage spelling, the renamed-header fork, the duplicated
'final' copy) is defined once, in the generator, and reused here.
"""

from __future__ import annotations

import importlib
import os
import random
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from simulator.profiles import profile_config
from simulator.world import WorldState


@dataclass
class _EngagementLike:
    """What the generator's reference-sheet builders expect to be handed."""
    cid: str
    client: str
    value: float
    events: list[dict]

    @property
    def last_ts(self) -> datetime:
        return self.events[-1]["ts"]


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    """tmp-then-os.replace, matching actions/store.py:44-50.

    pandas' openpyxl writer validates the destination's extension against
    {'.xlsx', '.xlsm'} and rejects a bare '.tmp' suffix outright, so the temp
    file keeps the real extension and is marked transient with a dot-prefix
    instead. It is written in the same directory as the target so the final
    os.replace stays on one volume (and therefore atomic).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}.",
                               suffix=path.suffix)
    os.close(fd)
    try:
        df.to_excel(tmp, index=False, engine="openpyxl")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _engagement_likes(world: WorldState, mod, rng: random.Random
                      ) -> list[_EngagementLike]:
    out = []
    for case in world.cases.values():
        rows = [{"case_id": case.cid, "activity": _mess(mod, e.stage, rng),
                 "ts": e.ts, "actor": e.actor, "status": e.status,
                 "value": case.value, "client": case.client}
                for e in case.events]
        out.append(_EngagementLike(cid=case.cid, client=case.client,
                                   value=case.value, events=rows))
    return out


def _mess(mod, stage: str, rng: random.Random) -> str:
    """The generator's _mess uses module-level random; reproduce its variants
    against our own rng so a tick stays deterministic."""
    return rng.choice([stage, stage.upper(), stage.lower(), stage + " "])


def event_dicts(world: WorldState, mod, rng: random.Random
                ) -> tuple[list[dict], list[dict]]:
    sales_stages = {s.lower() for s in mod.SALES_STAGES}
    sales, delivery = [], []
    for eng in _engagement_likes(world, mod, rng):
        for row in eng.events:
            target = (sales if row["activity"].strip().lower() in sales_stages
                      else delivery)
            target.append(row)
    sales.sort(key=lambda r: (r["ts"], r["case_id"]))
    delivery.sort(key=lambda r: (r["ts"], r["case_id"]))
    return sales, delivery


def render(world: WorldState, out_dir: Path, rng: random.Random) -> list[str]:
    cfg = profile_config(world.profile)
    mod = importlib.import_module(cfg["generator"])
    out_dir = Path(out_dir)

    engagements = _engagement_likes(world, mod, rng)
    sales, delivery = event_dicts(world, mod, rng)

    # Somebody saved a 'final' copy of the delivery sheet: older rows duplicated
    # verbatim, a few newer ones only there. Same rule as the static drive.
    fork_rows = delivery[:14] + delivery[-6:]

    writes = [
        ("leads.xlsx", mod._lead_frame(sales)),
        ("projects.xlsx", mod._project_frame(delivery)),
        ("projects - final NEW.xlsx", mod._project_frame(fork_rows)),
        ("clients.xlsx", mod.build_clients(engagements)),
        ("timesheets.xlsx", mod.build_timesheets(engagements)),
        ("team_capacity.xlsx", mod.build_team_capacity()),
        ("invoices.xlsx", mod.build_invoices(engagements)),
    ]
    for name, df in writes:
        _write_atomic(df, out_dir / name)
    return [name for name, _ in writes]
