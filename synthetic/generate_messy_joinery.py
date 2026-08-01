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

Nine jobs are also PARKED mid-flow and left — two with no fitter's name, six
held by one fitter. Those exercise the case-level rules (the action queue)
rather than the stage detector. The generator records only where each job
stopped; the rules decide whether that breaches anything.

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

# The drive's "today" — see the note in generate_messy_foyle.py.
AS_OF = datetime(2026, 6, 30)

# Jobs parked mid-flow. Mark holds six open jobs against a load limit of 4.
# The two-week-old Materials Ordered job (14-day SLA) and the two-week-old
# Invoice Sent job (30-day SLA) are parked but NOT in breach — the rules have
# to draw that line themselves.
_OPERATIONAL: list[dict] = [
    {"park": "Quote Sent",        "weeks": 3, "owner": "Mark Deehan"},
    {"park": "Quote Sent",        "weeks": 5, "unowned": True},
    {"park": "Quote Accepted",    "weeks": 4, "owner": "Mark Deehan"},
    {"park": "Materials Ordered", "weeks": 4, "owner": "Mark Deehan"},
    {"park": "Materials Ordered", "weeks": 2, "owner": "Mark Deehan"},
    {"park": "Site Work Started", "weeks": 5, "owner": "Mark Deehan"},
    {"park": "Snagging",          "weeks": 3, "owner": "Ciara McCrossan"},
    {"park": "Invoice Sent",      "weeks": 2, "unowned": True},
    {"park": "Site Survey",       "weeks": 4, "owner": "Mark Deehan"},
]


def build_operational() -> tuple[list[dict], dict]:
    """Jobs that stopped mid-flow, each translated so its LAST event sits
    exactly `weeks` before AS_OF. Built after the structural jobs so their
    random stream is untouched."""
    events: list[dict] = []
    intent: dict = {"parked_at": {}, "unowned": []}

    for i, plan in enumerate(_OPERATIONAL):
        cid = f"J-{1036 + i}"
        case = _job_events(cid, datetime(2026, 1, 1), delay=False,
                           rework=False, repetition=False,
                           park_at=plan["park"],
                           unowned=plan.get("unowned", False),
                           owner=plan.get("owner"))
        shift = (AS_OF - timedelta(weeks=plan["weeks"])) - case[-1]["ts"]
        for e in case:
            e["ts"] += shift
        events.extend(case)
        intent["parked_at"].setdefault(plan["park"], []).append(cid)
        if plan.get("unowned"):
            intent["unowned"].append(cid)

    intent["unowned"].sort()
    for stage in intent["parked_at"]:
        intent["parked_at"][stage].sort()
    return events, intent


def _mess(stage: str) -> str:
    return random.choice([stage, stage.upper(), stage.lower(), stage + " "])


def _job_events(cid: str, start: datetime, *, delay: bool, rework: bool,
                repetition: bool, park_at: str | None = None,
                unowned: bool = False,
                owner: str | None = None) -> list[dict]:
    """One job's event sequence.

    `park_at` truncates the job so it is left sitting at that stage; `unowned`
    leaves the Fitter cell blank; `owner` pins one fitter. All three default to
    the original behaviour, so synthetic/generate_stream.py is unaffected."""
    events: list[dict] = []
    t = start

    def add(stage: str, status: str | None = None) -> None:
        if status is None and park_at == stage:
            status = random.choice(OPEN_ISH)
        events.append({"case_id": cid, "activity": _mess(stage), "ts": t,
                       "actor": "" if unowned else (owner or random.choice(FITTERS)),
                       "status": status or random.choice(DONE_ISH)})

    def parked(stage: str) -> bool:
        return park_at == stage

    add("Quote Sent")
    if parked("Quote Sent"):
        return events
    t += timedelta(days=random.randint(2, 5))
    add("Quote Accepted")
    if parked("Quote Accepted"):
        return events
    t += timedelta(days=random.randint(1, 3))
    add("Site Survey")
    if repetition:
        # First measure missed what the workshop needed — the survey step is
        # entered a second time. A literal duplicate occurrence.
        t += timedelta(days=random.randint(1, 3))
        add("Site Survey", random.choice(OPEN_ISH))
    if parked("Site Survey"):
        return events
    t += timedelta(days=random.randint(1, 3))
    add("Materials Ordered")
    if parked("Materials Ordered"):
        return events
    # The delay signal: materials lead time into Site Work Started. 12-18
    # days sits safely above the worst-case dynamic outlier threshold
    # (normal gaps top out at 7 days).
    t += timedelta(days=random.randint(12, 18) if delay else random.randint(1, 3))
    add("Site Work Started")
    if parked("Site Work Started"):
        return events
    t += timedelta(days=random.randint(2, 5))
    add("Snagging")
    if rework:
        # Post-handover call-back: the job drops BACK to site work after
        # snagging. A genuine backward transition against stage_order.
        t += timedelta(days=random.randint(3, 6))
        add("Site Work Started", random.choice(OPEN_ISH))
    if parked("Snagging"):
        return events
    t += timedelta(days=random.randint(1, 3))
    add("Invoice Sent")
    if parked("Invoice Sent"):
        return events
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

    # Anchor the structural jobs on AS_OF, then build the parked ones (which
    # position themselves relative to AS_OF directly).
    shift = AS_OF - max(e["ts"] for e in main_events + fork_events)
    for e in main_events + fork_events:
        e["ts"] += shift
    operational, op_intent = build_operational()

    # Mark pasted a couple of main-sheet jobs into his personal copy too —
    # same rows, his headers. The approved mapping's dedup must absorb them.
    copied_cids = [f"J-{1021 + i}" for i in (4, 5)][:_N_COPIED_JOBS]
    copied = [e for cid in copied_cids
              for e in [x for x in main_events if x["case_id"] == cid][:_N_COPIED_EVENTS]]

    writes = [
        ("jobs 2026.xlsx", _frame(main_events + operational, MAIN_HEADERS, "%d/%m/%Y")),
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
    n_jobs = len({e["case_id"] for e in main_events + fork_events + operational})
    payload = {"generated_at": datetime.now().isoformat(), "seed": SEED,
               "as_of": AS_OF.isoformat(), "cases": n_jobs,
               "bottlenecks": gt, "operational_intent": op_intent}
    GT_BOTTLENECKS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Ground truth: {GT_MAPPING_FILE.name}, {GT_BOTTLENECKS_FILE.name}")
    print(f"Seeded over {n_jobs} jobs: {len(gt['delay'])} delay, "
          f"{len(gt['repetition'])} repetition, {len(gt['rework'])} rework")
    parked = {k: len(v) for k, v in op_intent["parked_at"].items()}
    print(f"Parked jobs by stage: {parked}  "
          f"unowned: {len(op_intent['unowned'])}")


if __name__ == "__main__":
    main()
