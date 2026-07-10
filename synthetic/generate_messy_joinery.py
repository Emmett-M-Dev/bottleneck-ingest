"""Generate the messy joinery drive — the CONTRASTING SME profile.

Same observed mess patterns as the Foyle drive (a personal fork with renamed
headers, duplicated rows, freetext notes, an irrelevant file) over a
completely different workflow vocabulary: a joinery firm's job pipeline
(quote -> materials -> site work -> snagging -> invoice). The pipeline
ingests it through the identical audit -> approve -> mapped-reader path with
zero new reader code — that is the generalisability claim.

    jobs 2026.xlsx              the main job sheet (Job No/Stage/Date/Fitter/Notes)
    jobs spring - Marks copy.xlsx  one fitter's personal copy — renamed headers
                                   (Job #/Phase/When/Who/Comments), holds his
                                   spring jobs plus rows copied from the main sheet
    materials orders.xlsx       reference data (suppliers, no event structure)
    old quotes 2024.xlsx        stale, irrelevant to the 2026 workflow

Seeded bottlenecks (mirrored into ground_truth_messy_joinery.json). All
three are STRUCTURAL — patterns in the event sequence, not marker stages —
so the dynamic detector must find them from the data alone:

    delay       — a long materials lead time into Site Work Started (vs the
                  log's own gap distribution)
    repetition  — Site Survey entered twice (first measure missed what the
                  workshop needed)
    rework      — the job drops BACK to Site Work Started after Snagging
                  (post-handover call-back)

EVERYTHING is synthetic. Run:  python synthetic/generate_messy_joinery.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

SEED = 17
PROFILE = config.MESSY_PROFILES["joinery"]
OUT_DIR: Path = PROFILE["dir"]
GT_MAPPING_FILE: Path = PROFILE["gt_mapping"]
GT_BOTTLENECKS_FILE: Path = PROFILE["gt_bottlenecks"]

MAIN_HEADERS = ["Job No", "Stage", "Date", "Fitter", "Notes"]
FORK_HEADERS = ["Job #", "Phase", "When", "Who", "Comments"]

FITTERS = ["Mark Deehan", "Paddy Lynch", "Ciara McCrossan"]

DONE_ISH = ["done", "Done ", "complete", "sorted", "ok"]
OPEN_ISH = ["waiting on glass", "supplier late", "client to confirm", "", "tbc"]

_N_COPIED_JOBS = 2    # main-sheet jobs Mark also pasted into his copy
_N_COPIED_EVENTS = 3


def _mess(stage: str) -> str:
    return random.choice([stage, stage.upper(), stage.lower(), stage + " "])


def _job_events(cid: str, start: datetime, *, delay: bool, rework: bool,
                repetition: bool) -> list[dict]:
    events: list[dict] = []
    t = start

    def add(stage: str, status: str | None = None) -> None:
        events.append({"case_id": cid, "activity": _mess(stage), "ts": t,
                       "actor": random.choice(FITTERS),
                       "status": status or random.choice(DONE_ISH)})

    add("Quote Sent")
    t += timedelta(days=random.randint(2, 5))
    add("Quote Accepted")
    t += timedelta(days=random.randint(1, 3))
    add("Site Survey")
    if repetition:
        # First measure missed what the workshop needed — the survey step is
        # entered a second time. A literal duplicate occurrence.
        t += timedelta(days=random.randint(1, 3))
        add("Site Survey", random.choice(OPEN_ISH))
    t += timedelta(days=random.randint(1, 3))
    add("Materials Ordered")
    # The delay signal: materials lead time into Site Work Started. 12-18
    # days sits safely above the worst-case dynamic outlier threshold
    # (normal gaps top out at 7 days).
    t += timedelta(days=random.randint(12, 18) if delay else random.randint(1, 3))
    add("Site Work Started")
    t += timedelta(days=random.randint(2, 5))
    add("Snagging")
    if rework:
        # Post-handover call-back: the job drops BACK to site work after
        # snagging. A genuine backward transition against stage_order.
        t += timedelta(days=random.randint(3, 6))
        add("Site Work Started", random.choice(OPEN_ISH))
    t += timedelta(days=random.randint(1, 3))
    add("Invoice Sent")
    t += timedelta(days=random.randint(3, 7))
    add("Payment Received")
    return events


def build_jobs() -> tuple[list[dict], list[dict], dict]:
    gt = {"delay": [], "repetition": [], "rework": []}

    def flags(i: int, delay_at: set, rework_at: set, rep_at: set) -> dict:
        return {"delay": i in delay_at, "rework": i in rework_at,
                "repetition": i in rep_at}

    main: list[dict] = []
    for i in range(9):
        cid = f"J-{1021 + i}"
        f = flags(i, {1, 5}, {2, 7}, {4})
        start = datetime(2026, 1, 12) + timedelta(days=i * 12 + random.randint(0, 3))
        main.extend(_job_events(cid, start, **f))
        for kind, hit in f.items():
            if hit:
                gt[kind].append(cid)

    fork: list[dict] = []
    for i in range(6):
        cid = f"J-{1030 + i}"
        f = flags(i, {2}, {4}, {1})
        start = datetime(2026, 3, 9) + timedelta(days=i * 9 + random.randint(0, 3))
        fork.extend(_job_events(cid, start, **f))
        for kind, hit in f.items():
            if hit:
                gt[kind].append(cid)

    for kind in gt:
        gt[kind].sort()
    return main, fork, gt


def _frame(events: list[dict], headers: list[str], date_fmt: str) -> pd.DataFrame:
    rows = [{headers[0]: e["case_id"], headers[1]: e["activity"],
             headers[2]: e["ts"].strftime(date_fmt), headers[3]: e["actor"],
             headers[4]: e["status"]} for e in events]
    return pd.DataFrame(rows, columns=headers)


def build_materials() -> pd.DataFrame:
    rows = [
        {"Supplier": "Derry Glazing Ltd", "Material": "Toughened glass units", "Lead Time": "10 days", "Account": "DG-114"},
        {"Supplier": "North West Timber", "Material": "Oak worktop blanks",    "Lead Time": "5 days",  "Account": "NWT-27"},
        {"Supplier": "Foyle Fixings",     "Material": "Hinges & runners",      "Lead Time": "2 days",  "Account": "FF-081"},
        {"Supplier": "Sperrin Sprays",    "Material": "Spray finishing",       "Lead Time": "7 days",  "Account": "SS-9"},
    ]
    return pd.DataFrame(rows)


def build_old_quotes() -> pd.DataFrame:
    rows = [
        {"Quote Ref": "Q-2024-31", "Client": "The Nashes",  "Job": "Wardrobes",     "Amount": 2400, "Outcome": "lost"},
        {"Quote Ref": "Q-2024-44", "Client": "The Harkins", "Job": "Kitchen units", "Amount": 6800, "Outcome": "won"},
        {"Quote Ref": "Q-2024-58", "Client": "The Barrs",   "Job": "Staircase",     "Amount": 3900, "Outcome": "no reply"},
    ]
    return pd.DataFrame(rows)


def _ground_truth_mapping() -> dict:
    canonical = ["case_id", "activity", "timestamp", "actor", "status"]
    return {
        "profile": "joinery",
        "seed": SEED,
        "files": [
            {"filename": "jobs 2026.xlsx", "sheet": "Sheet1", "role": "events",
             "include": True, "columns": dict(zip(MAIN_HEADERS, canonical))},
            {"filename": "jobs spring - Marks copy.xlsx", "sheet": "Sheet1",
             "role": "events", "include": True,
             "columns": dict(zip(FORK_HEADERS, canonical))},
            {"filename": "materials orders.xlsx", "sheet": "Sheet1",
             "role": "reference", "include": True, "columns": {}},
            {"filename": "old quotes 2024.xlsx", "sheet": "Sheet1",
             "role": "ignore", "include": False, "columns": {}},
        ],
        "expected_issues": {
            "duplicates": ["jobs spring - Marks copy.xlsx repeats rows of jobs 2026.xlsx"],
            "overlaps": ["jobs 2026.xlsx and jobs spring - Marks copy.xlsx both cover spring 2026 jobs"],
            "gaps": [],
            "irrelevant": ["old quotes 2024.xlsx"],
        },
    }


def main() -> None:
    random.seed(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    main_events, fork_events, gt = build_jobs()

    # Mark pasted a couple of main-sheet jobs into his personal copy too —
    # same rows, his headers. The approved mapping's dedup must absorb them.
    copied_cids = [f"J-{1021 + i}" for i in (4, 5)][:_N_COPIED_JOBS]
    copied = [e for cid in copied_cids
              for e in [x for x in main_events if x["case_id"] == cid][:_N_COPIED_EVENTS]]

    writes = [
        ("jobs 2026.xlsx", _frame(main_events, MAIN_HEADERS, "%d/%m/%Y")),
        ("jobs spring - Marks copy.xlsx",
         _frame(fork_events + copied, FORK_HEADERS, "%Y-%m-%d")),
        ("materials orders.xlsx", build_materials()),
        ("old quotes 2024.xlsx", build_old_quotes()),
    ]
    for name, df in writes:
        df.to_excel(OUT_DIR / name, index=False, engine="openpyxl")
        print(f"Wrote {df.shape[0]:3d} rows -> {OUT_DIR / name}")

    GT_MAPPING_FILE.write_text(json.dumps(_ground_truth_mapping(), indent=2),
                               encoding="utf-8")
    n_jobs = len({e["case_id"] for e in main_events + fork_events})
    payload = {"generated_at": datetime.now().isoformat(), "seed": SEED,
               "cases": n_jobs, "bottlenecks": gt}
    GT_BOTTLENECKS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Ground truth: {GT_MAPPING_FILE.name}, {GT_BOTTLENECKS_FILE.name}")
    print(f"Seeded over {n_jobs} jobs: {len(gt['delay'])} delay, "
          f"{len(gt['repetition'])} repetition, {len(gt['rework'])} rework")


if __name__ == "__main__":
    main()
