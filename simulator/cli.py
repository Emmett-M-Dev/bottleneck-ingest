"""Simulator entry point.

    python -m simulator.cli --profile advisory --reset
    python -m simulator.cli --profile advisory --advance 7
    python -m simulator.cli --profile advisory --status

One JSON object on stdout per invocation, so a FastAPI orchestrator can shell
out to this exactly as it already shells out to the pipeline. The headless eval
calls advance() directly and shares the same code path.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
from pathlib import Path

import config
from actions import store
from simulator.step import advance
from simulator.world import WorldState, day0_from_generator, from_dict

_ACTIONABLE = {"approved", "assigned", "in_progress"}


def _paths(profile: str) -> dict[str, Path]:
    root = Path(config.DATA_SIM) / profile
    return {"root": root, "state": root / "state.json",
            "drive": root / "drive", "inbox": root / "inbox.jsonl",
            "cache": root / "cache"}


def load_world(profile: str) -> WorldState:
    state = _paths(profile)["state"]
    if state.exists():
        return from_dict(json.loads(state.read_text(encoding="utf-8")))
    return day0_from_generator(profile)


def save_world(world: WorldState) -> Path:
    path = _paths(world.profile)["state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    Path(tmp).write_text(json.dumps(world.to_dict(), indent=2),
                         encoding="utf-8")
    os.replace(tmp, path)
    return path


def append_inbox(profile: str, messages) -> None:
    path = _paths(profile)["inbox"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps(m.to_dict()) + "\n")


def approved_items(profile: str, path: Path | None = None) -> list:
    """Read-only peek at the product's action store — the ONLY product state
    the simulator ever reads."""
    return [i for i in store.load_actions(profile, path)
            if i.status in _ACTIONABLE]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", default="advisory")
    ap.add_argument("--advance", type=int, default=0, metavar="N")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--llm", action="store_true",
                    help="fill message slots with Claude (cached)")
    ap.add_argument("--approved-from", type=Path, default=None,
                    help="action store to read approvals from")
    args = ap.parse_args(argv)

    paths = _paths(args.profile)

    if args.reset:
        world = day0_from_generator(args.profile)
        if paths["inbox"].exists():
            paths["inbox"].unlink()
        save_world(world)
        from simulator import render as render_mod
        render_mod.render(world, paths["drive"], random.Random(world.seed))
        print(json.dumps({"profile": args.profile, "day": 0,
                          "cases": len(world.cases),
                          "drive": str(paths["drive"])}))
        return

    world = load_world(args.profile)

    if args.status or args.advance <= 0:
        print(json.dumps({"profile": args.profile, "day": world.day,
                          "date": world.current_date.date().isoformat(),
                          "cases": len(world.cases),
                          "drive": str(paths["drive"])}))
        return

    approved = approved_items(args.profile, args.approved_from)
    days = []
    for _ in range(args.advance):
        result = advance(world, approved, drive_dir=paths["drive"],
                         cache_dir=paths["cache"], use_llm=args.llm)
        append_inbox(args.profile, result.messages)
        days.append(result.to_dict())
    save_world(world)

    print(json.dumps({"profile": args.profile, "day": world.day,
                      "date": world.current_date.date().isoformat(),
                      "cases": len(world.cases),
                      "drive": str(paths["drive"]), "days": days}))


if __name__ == "__main__":
    sys.exit(main())
