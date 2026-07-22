"""Generate the STREAM drives — weekly snapshots for the longitudinal replay.

The static messy drives prove the pipeline works on one snapshot. This
builder produces the *dynamic* test bed: the same messy drive as it would
have looked at the end of each week, so eval/replay.py can replay time
through the unchanged pipeline core and measure how detection tracks a
moving truth and how the learning loop compounds.

    data/synthetic/stream_<profile>/
        tick_01/ *.xlsx     the drive at the end of week 1
        tick_02/ *.xlsx     week 2 — same files, rows appended, new cases
        ...
        tick_09/ *.xlsx     week 9

Each snapshot is CUMULATIVE (staff append rows; old rows stay), so ingest
reads a tick directory exactly like any other messy drive — zero reader
changes. The file set is fixed across ticks (mapping approved once); a new
file appearing mid-stream would re-fire Gate 1 and is noted as future work.

Cases arrive 2-3 per week over weeks 1-6 and unfold over the following
weeks, split between a canonical-headers sheet and a renamed-headers fork
(the seasonal-fork mess from the static drives, kept alive here). Injected
patterns reuse the SAME event builders as the static generators
(generate_messy_foyle._case_events / generate_messy_joinery._job_events),
so injection logic stays in one place and stays separate from detection
logic (the circularity guard).

Per-tick ground truth: a pattern enters the truth at the tick where the
event that COMPLETES it becomes visible (the delayed entry lands, the second
occurrence appears, the backward transition is written) — computed from the
injection flags + marker stages, never from the detector. Ground truth,
embedded mapping (replay ingests via the GT mapping, isolating the
longitudinal eval from mapping quality, as eval/score_detection does) and
the full schedule go to ground_truth_stream_<profile>.json.

EVERYTHING is synthetic.  Run:  python synthetic/generate_stream.py --profile foyle
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from synthetic import generate_messy_foyle as _foyle
from synthetic import generate_messy_joinery as _joinery

SEED = 23

# Arrival schedule: (week, injected pattern | None), one entry per case.
# Pairs arrive early so min_affected=2 patterns become reportable mid-stream;
# later singles keep each pattern type recurring — the learning loop's chance
# to retrieve an earlier approved fix. Late weeks stay clean: their cases are
# still unfolding when the stream ends, like real ongoing operations.
_FOYLE_SCHEDULE = [
    (1, "delay"), (1, "rework"), (1, "delay"),
    (2, "repetition"), (2, "rework"),
    (3, "repetition"), (3, None),
    (4, "delay"), (4, None),
    (5, "repetition"), (5, "rework"), (5, "delay"),
    (6, None), (6, None),
]
# Joinery sequences run longer (payment tail), so its last injections sit a
# week earlier to land inside the stream window.
_JOINERY_SCHEDULE = [
    (1, "repetition"), (1, "delay"),
    (2, "delay"), (2, "rework"), (2, None),
    (3, "rework"), (3, "repetition"),
    (4, "delay"), (4, "delay"),
    (5, "repetition"), (5, "rework"), (5, None),
    (6, None), (6, None),
]

# Everything profile-specific the slicer needs; the slicing itself is shared.
RECIPES = {
    "foyle": {
        "builder": _foyle._case_events,
        "case_id_fmt": "B-26S-{:03d}",
        "start": datetime(2026, 3, 2),          # spring cohort, distinct from the static drives
        "schedule": _FOYLE_SCHEDULE,
        "event_files": [                        # (filename, headers, date_fmt); cases alternate
            ("bookings tracker 2026.xlsx", _foyle.CANON_HEADERS, "%d/%m/%Y"),
            ("bookings tracker - summer copy.xlsx", _foyle.SUMMER_HEADERS, "%Y-%m-%d"),
        ],
        "static_files": [                       # (filename, builder, role, include) — identical every tick
            ("host families 2026.xlsx", _foyle.build_host_families, "reference", True),
            ("staff phone list.xlsx", _foyle.build_staff_phone_list, "ignore", False),
        ],
    },
    "joinery": {
        "builder": _joinery._job_events,
        "case_id_fmt": "J-26S-{:03d}",
        "start": datetime(2026, 3, 2),
        "schedule": _JOINERY_SCHEDULE,
        "event_files": [
            ("jobs tracker 2026.xlsx", _joinery.MAIN_HEADERS, "%d/%m/%Y"),
            ("jobs tracker - Marks copy.xlsx", _joinery.FORK_HEADERS, "%Y-%m-%d"),
        ],
        "static_files": [
            ("materials orders.xlsx", _joinery.build_materials, "reference", True),
            ("old quotes 2024.xlsx", _joinery.build_old_quotes, "ignore", False),
        ],
    },
}

KINDS = ("delay", "repetition", "rework")


def _canon(label: str) -> str:
    return str(label).strip().lower()


def _visible_ts(events: list[dict], kind: str, markers: dict) -> datetime:
    """When the injected pattern becomes visible in the log: the delayed
    entry's own timestamp, or the second occurrence of the repeated /
    looped-back stage. Injection knowledge, not detection logic."""
    stage = _canon(markers[f"{kind}_stage"])
    occ = [e["ts"] for e in events if _canon(e["activity"]) == stage]
    return occ[0] if kind == "delay" else occ[1]


def build_cases(recipe: dict, markers: dict) -> tuple[list[dict], list[dict]]:
    """Returns (cases, all_events). Each case dict: cid, file index, injected
    kind, pattern-visible timestamp."""
    cases: list[dict] = []
    events: list[dict] = []
    for i, (week, kind) in enumerate(recipe["schedule"]):
        cid = recipe["case_id_fmt"].format(i + 1)
        start = (recipe["start"] + timedelta(days=(week - 1) * 7 + random.randint(0, 4)))
        flags = {k: k == kind for k in KINDS}
        evs = recipe["builder"](cid, start, **flags)
        cases.append({
            "cid": cid, "week": week, "file_idx": i % 2, "kind": kind,
            "visible_ts": _visible_ts(evs, kind, markers) if kind else None,
        })
        events.extend(evs)
    return cases, events


def _frame(events: list[dict], headers: list[str], date_fmt: str) -> pd.DataFrame:
    rows = [{headers[0]: e["case_id"], headers[1]: e["activity"],
             headers[2]: e["ts"].strftime(date_fmt), headers[3]: e["actor"],
             headers[4]: e["status"]} for e in events]
    return pd.DataFrame(rows, columns=headers)


def _mapping(recipe: dict, profile: str) -> dict:
    """The ground-truth mapping for the stream drive (same shape as the static
    gt_mapping files — replay builds an ApprovedMapping straight from it)."""
    canonical = ["case_id", "activity", "timestamp", "actor", "status"]
    files = [{"filename": name, "sheet": "Sheet1", "role": "events",
              "include": True, "columns": dict(zip(headers, canonical))}
             for name, headers, _ in recipe["event_files"]]
    files += [{"filename": name, "sheet": "Sheet1", "role": role,
               "include": include, "columns": {}}
              for name, _, role, include in recipe["static_files"]]
    return {"profile": profile, "files": files}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", choices=sorted(RECIPES), default="foyle")
    args = ap.parse_args()

    random.seed(SEED)
    recipe = RECIPES[args.profile]
    markers = config.MESSY_PROFILES[args.profile]["markers"]
    out_root = config.stream_dir(args.profile)
    if out_root.exists():
        shutil.rmtree(out_root)          # regenerating must not leave stale ticks

    cases, all_events = build_cases(recipe, markers)
    by_case = {c["cid"]: c for c in cases}

    ticks_meta: list[dict] = []
    for t in range(1, config.STREAM_TICKS + 1):
        cutoff = recipe["start"] + timedelta(days=config.STREAM_TICK_DAYS * t)
        visible = [e for e in all_events if e["ts"] <= cutoff]

        tick_dir = out_root / f"tick_{t:02d}"
        tick_dir.mkdir(parents=True)
        for idx, (name, headers, date_fmt) in enumerate(recipe["event_files"]):
            evs = [e for e in visible if by_case[e["case_id"]]["file_idx"] == idx]
            _frame(evs, headers, date_fmt).to_excel(
                tick_dir / name, index=False, engine="openpyxl")
        for name, builder, _, _ in recipe["static_files"]:
            builder().to_excel(tick_dir / name, index=False, engine="openpyxl")

        gt_t = {k: sorted(c["cid"] for c in cases
                          if c["kind"] == k and c["visible_ts"] <= cutoff)
                for k in KINDS}
        ticks_meta.append({
            "tick": t, "cutoff": cutoff.date().isoformat(),
            "cases_visible": len({e["case_id"] for e in visible}),
            "events_visible": len(visible),
            "bottlenecks": gt_t,
        })
        counts = "/".join(str(len(gt_t[k])) for k in KINDS)
        print(f"tick_{t:02d}  cutoff {cutoff.date()}  "
              f"{len(visible):3d} events  gt d/rp/rw: {counts}")

    payload = {
        "generated_at": datetime.now().isoformat(), "seed": SEED,
        "profile": args.profile, "tick_days": config.STREAM_TICK_DAYS,
        "start": recipe["start"].date().isoformat(),
        "mapping": _mapping(recipe, args.profile),
        "schedule": [{"cid": c["cid"], "week": c["week"], "injected": c["kind"]}
                     for c in cases],
        "ticks": ticks_meta,
    }
    gt_path = config.stream_gt_path(args.profile)
    gt_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Ground truth -> {gt_path.name}")


if __name__ == "__main__":
    main()
