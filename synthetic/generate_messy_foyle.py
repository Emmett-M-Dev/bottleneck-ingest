"""Generate the deliberately messy Foyle drive — the mapping-agent test bed.

The SharePoint audit found the real failure mode isn't missing data, it's
*sedimented* data: a bookings sheet gets used for a few months, then someone
starts a fresh one for the summer intake with renamed columns, the old copy
lingers, statuses are freetext, and unrelated files sit in the same folder.
This builder replicates exactly those observed patterns (documented in the
methodology) so the audit agent has a grounded mess to untangle:

    bookings Jan-May 2026.xlsx            canonical-ish headers (Booking Ref/Stage/...)
    bookings summer NEW.xlsx              the seasonal fork — same data shape, headers
                                          renamed (Ref #/Step/Updated On/...), ISO dates
    bookings summer OLD do not use.xlsx   stale duplicate of the first summer rows,
                                          back in the old header style + dd/mm/yyyy dates
    host families 2026.xlsx               reference data (no event structure)
    staff phone list.xlsx                 irrelevant file (names + phone numbers — also
                                          the zero-PII-to-API test material)
    (nothing covers June — a genuine gap the mess report should flag)

Seeded bottlenecks span BOTH bookings forks (mirrored into
ground_truth_messy_foyle.json — proving the approved mapping unlocks
detection across the fork is the point). All three are STRUCTURAL — patterns
in the event sequence itself, not helpfully-named marker stages — so the
dynamic detector must find them from the data alone:

    delay       — a long gap into Booking Confirmed (vs the log's own
                  gap distribution)
    repetition  — Document Collection entered twice (papers lost between
                  seasonal sheets, collected again)
    rework      — the case drops BACK to Placement Offer after Booking
                  Confirmed (host fell through post-confirmation)

Alongside the structural patterns, ten bookings are PARKED mid-flow and left
(some unowned, most held by one coordinator). Those exercise the case-level
rules in detection/case_rules.py — the action queue — rather than the stage
detector. The generator records only where each booking stopped
(operational_intent in the ground truth); whether stopping there breaches an
SLA is the rules' decision, not the generator's.

ground_truth_mapping_foyle.json records the true per-file role + column map
the audit agent must rediscover. EVERYTHING is synthetic.

Run:  python synthetic/generate_messy_foyle.py
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

SEED = 13
PROFILE = config.MESSY_PROFILES["foyle"]
OUT_DIR: Path = PROFILE["dir"]
GT_MAPPING_FILE: Path = PROFILE["gt_mapping"]
GT_BOTTLENECKS_FILE: Path = PROFILE["gt_bottlenecks"]

# Old-template headers (aliases of config.COLUMN_MAP — the heuristic baseline
# resolves these) vs the summer fork's renamed headers (it should not).
CANON_HEADERS  = ["Booking Ref", "Stage", "Date", "Handled By", "Status"]
SUMMER_HEADERS = ["Ref #", "Step", "Updated On", "Staff Member", "Payment status"]

# Fabricated staff — masked wholesale by scrub_actor downstream.
STAFF = ["Aoife Brennan", "Marta Kowalska", "Sean Doyle"]

# Freetext status mess, as observed: no vocabulary, ticks, trailing spaces.
DONE_ISH = ["Done", "done ", "DONE", "complete", "Completed ✔", "ok"]
OPEN_ISH = ["In progress", "waiting on family", "chased 2x", "", "tbc"]

_N_OLD_CASES = 4      # summer cases duplicated into the stale OLD file
_N_OLD_EVENTS = 3     # first N events of each duplicated case

# The drive's "today". Everything is shifted so the newest event lands here,
# which makes staleness a property of the seed rather than of when the
# generator happened to be run. detection/case_rules.py takes the newest
# event in the log as its `as_of`, so this is the anchor the rules use.
AS_OF = datetime(2026, 8, 31)

# Bookings parked mid-flow and left. `weeks` = how stale the booking is at
# AS_OF. Aoife holds seven open bookings, which is over the profile's
# owner_load_limit of 6 — the concentration is deliberate. The Invoice Issued
# booking is only a fortnight old against a 30-day SLA, so it is parked but
# NOT in breach: the rules must decide that for themselves.
_OPERATIONAL: list[dict] = [
    {"park": "Request Received",     "weeks": 3, "owner": "Aoife Brennan"},
    {"park": "Request Received",     "weeks": 4, "unowned": True},
    {"park": "Placement Offer",      "weeks": 5, "owner": "Aoife Brennan"},
    {"park": "Placement Offer",      "weeks": 6, "unowned": True},
    {"park": "Booking Confirmed",    "weeks": 4, "owner": "Aoife Brennan"},
    {"park": "Booking Confirmed",    "weeks": 3, "owner": "Aoife Brennan"},
    {"park": "Document Collection",  "weeks": 3, "owner": "Aoife Brennan"},
    {"park": "Pre-Arrival Logistics","weeks": 5, "owner": "Aoife Brennan"},
    {"park": "Pre-Arrival Logistics","weeks": 4, "owner": "Aoife Brennan"},
    {"park": "Invoice Issued",       "weeks": 2, "owner": "Marta Kowalska"},
]


def build_operational() -> tuple[list[dict], dict]:
    """Bookings that stopped mid-flow. Each case is translated so its LAST
    event sits exactly `weeks` before AS_OF.

    Built AFTER the structural cases so the random stream those cases draw
    from is untouched — the seeded bottlenecks stay byte-identical."""
    events: list[dict] = []
    intent: dict = {"parked_at": {}, "unowned": []}

    for i, plan in enumerate(_OPERATIONAL):
        cid = f"B-2026-{19 + i:03d}"
        case = _case_events(cid, datetime(2026, 1, 1), delay=False,
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
    """Canonical-modulo-case/whitespace — staff copy labels but not carefully."""
    return random.choice([stage, stage.upper(), stage.lower(), stage + " "])


def _case_events(cid: str, start: datetime, *, delay: bool, rework: bool,
                 repetition: bool, park_at: str | None = None,
                 unowned: bool = False,
                 owner: str | None = None) -> list[dict]:
    """One booking's event sequence. Timestamps stay datetimes; each output
    file formats them in its own (inconsistent) style.

    `park_at` truncates the booking so it is left sitting at that stage —
    the operational patterns are all "somebody stopped here and nobody came
    back". `unowned` blanks the handler column (staff genuinely leave it
    empty); `owner` pins one name so the load rules have something real to
    find. All three default to the original behaviour, so every existing
    caller — including synthetic/generate_stream.py — is unaffected."""
    events: list[dict] = []
    t = start

    def add(stage: str, status: str | None = None) -> None:
        if status is None and park_at == stage:
            status = random.choice(OPEN_ISH)
        events.append({"case_id": cid, "activity": _mess(stage), "ts": t,
                       "actor": "" if unowned else (owner or random.choice(STAFF)),
                       "status": status or random.choice(DONE_ISH)})

    def parked(stage: str) -> bool:
        return park_at == stage

    add("Request Received")
    if parked("Request Received"):
        return events
    t += timedelta(days=random.randint(1, 3))
    add("Placement Offer")
    if parked("Placement Offer"):
        return events
    # The delay signal: a long gap from the prior event into Booking Confirmed.
    # 12-18 days sits safely above the worst-case dynamic outlier threshold
    # (normal gaps top out at 6 days, so Q3+1.5*IQR cannot reach 12).
    t += timedelta(days=random.randint(12, 18) if delay else random.randint(1, 3))
    add("Booking Confirmed")
    if parked("Booking Confirmed"):
        return events
    if rework:
        # Host fell through AFTER confirmation — the case drops back to
        # Placement Offer. A genuine backward transition against stage_order.
        t += timedelta(days=random.randint(2, 5))
        add("Placement Offer", random.choice(OPEN_ISH))
    t += timedelta(days=random.randint(1, 4))
    add("Invoice Issued")
    if parked("Invoice Issued"):
        return events
    t += timedelta(days=random.randint(1, 3))
    add("Document Collection")
    if repetition:
        # Papers lost between seasonal sheets — the same collection step is
        # entered a second time. A literal duplicate occurrence.
        t += timedelta(days=random.randint(1, 3))
        add("Document Collection", random.choice(OPEN_ISH))
    if parked("Document Collection"):
        return events
    t += timedelta(days=random.randint(2, 6))
    add("Pre-Arrival Logistics")
    if parked("Pre-Arrival Logistics"):
        return events
    t += timedelta(days=random.randint(3, 6))
    add("Arrival")
    return events


def build_bookings() -> tuple[list[dict], list[dict], dict]:
    """Returns (jan_may_events, summer_events, ground_truth). The June gap is
    structural: Jan-May cases finish by late May, summer cases start in July."""
    gt = {"delay": [], "repetition": [], "rework": []}

    def flags(i: int, delay_at: set, rework_at: set, rep_at: set) -> dict:
        return {"delay": i in delay_at, "rework": i in rework_at,
                "repetition": i in rep_at}

    jan_may: list[dict] = []
    for i in range(10):
        cid = f"B-2026-{i + 1:03d}"
        f = flags(i, {0, 4, 8}, {1, 5}, {2, 6})
        start = datetime(2026, 1, 6) + timedelta(days=i * 13 + random.randint(0, 3))
        jan_may.extend(_case_events(cid, start, **f))
        for kind, hit in f.items():
            if hit:
                gt[kind].append(cid)

    summer: list[dict] = []
    for i in range(8):
        cid = f"B-2026-{i + 11:03d}"
        f = flags(i, {1, 5}, {2}, {4})
        start = datetime(2026, 7, 1) + timedelta(days=i * 4 + random.randint(0, 2))
        summer.extend(_case_events(cid, start, **f))
        for kind, hit in f.items():
            if hit:
                gt[kind].append(cid)

    for kind in gt:
        gt[kind].sort()
    return jan_may, summer, gt


def _frame(events: list[dict], headers: list[str], date_fmt: str) -> pd.DataFrame:
    rows = [{headers[0]: e["case_id"], headers[1]: e["activity"],
             headers[2]: e["ts"].strftime(date_fmt), headers[3]: e["actor"],
             headers[4]: e["status"]} for e in events]
    return pd.DataFrame(rows, columns=headers)


def build_host_families() -> pd.DataFrame:
    rows = [
        {"Family Name": "The Dohertys",  "Address": "12 Rossville St",  "Area": "Cityside",  "Capacity": 2, "Notes": "no pets"},
        {"Family Name": "The Hegartys",  "Address": "4 Marlborough Ter", "Area": "Cityside",  "Capacity": 1, "Notes": ""},
        {"Family Name": "The Cassidys",  "Address": "31 Limavady Rd",   "Area": "Waterside", "Capacity": 3, "Notes": "vegetarian meals ok"},
        {"Family Name": "The McLaughlins", "Address": "8 Culmore Pt",   "Area": "Culmore",   "Capacity": 2, "Notes": "prefers summer only"},
        {"Family Name": "The Quigleys",  "Address": "22 Strabane Old Rd", "Area": "Waterside", "Capacity": 2, "Notes": ""},
        {"Family Name": "The Gallaghers", "Address": "3 Buncrana Rd",   "Area": "Pennyburn", "Capacity": 4, "Notes": "two rooms"},
    ]
    return pd.DataFrame(rows)


def build_staff_phone_list() -> pd.DataFrame:
    rows = [
        {"Name": "Aoife Brennan",  "Role": "Placements",    "Mobile": "+44 7700 900123"},
        {"Name": "Marta Kowalska", "Role": "Accommodation", "Mobile": "+44 7700 900456"},
        {"Name": "Sean Doyle",     "Role": "Logistics",     "Mobile": "+44 7700 900789"},
        {"Name": "Una Toner",      "Role": "Finance",       "Mobile": "+44 7700 900321"},
    ]
    return pd.DataFrame(rows)


def _ground_truth_mapping() -> dict:
    canon = dict(zip(CANON_HEADERS,
                     ["case_id", "activity", "timestamp", "actor", "status"]))
    summer = dict(zip(SUMMER_HEADERS,
                      ["case_id", "activity", "timestamp", "actor", "status"]))
    return {
        "profile": "foyle",
        "seed": SEED,
        "files": [
            {"filename": "bookings Jan-May 2026.xlsx", "sheet": "Sheet1",
             "role": "events", "include": True, "columns": canon},
            {"filename": "bookings summer NEW.xlsx", "sheet": "Sheet1",
             "role": "events", "include": True, "columns": summer},
            {"filename": "bookings summer OLD do not use.xlsx", "sheet": "Sheet1",
             "role": "events", "include": False, "columns": canon},
            {"filename": "host families 2026.xlsx", "sheet": "Sheet1",
             "role": "reference", "include": True, "columns": {}},
            {"filename": "staff phone list.xlsx", "sheet": "Sheet1",
             "role": "ignore", "include": False, "columns": {}},
        ],
        "expected_issues": {
            "duplicates": ["bookings summer OLD do not use.xlsx duplicates the "
                           "first rows of bookings summer NEW.xlsx"],
            "overlaps": [],
            "gaps": ["No bookings cover June 2026"],
            "irrelevant": ["staff phone list.xlsx"],
        },
    }


def main() -> None:
    random.seed(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    jan_may, summer, gt = build_bookings()

    # Anchor the structural cases on AS_OF, then build the parked ones (which
    # position themselves relative to AS_OF directly).
    shift = AS_OF - max(e["ts"] for e in jan_may + summer)
    for e in jan_may + summer:
        e["ts"] += shift
    operational, op_intent = build_operational()

    # The stale duplicate: first events of the first summer cases, re-saved in
    # the OLD header style and date format. Same case/activity/timestamp after
    # parsing -> the mapped reader's dedup must neutralise it.
    old_cids = [f"B-2026-{i + 11:03d}" for i in range(_N_OLD_CASES)]
    old_events = [e for cid in old_cids
                  for e in [x for x in summer if x["case_id"] == cid][:_N_OLD_EVENTS]]

    writes = [
        ("bookings Jan-May 2026.xlsx", _frame(jan_may, CANON_HEADERS, "%d/%m/%Y")),
        ("bookings summer NEW.xlsx",
         _frame(summer + operational, SUMMER_HEADERS, "%Y-%m-%d")),
        ("bookings summer OLD do not use.xlsx", _frame(old_events, CANON_HEADERS, "%d/%m/%Y")),
        ("host families 2026.xlsx", build_host_families()),
        ("staff phone list.xlsx", build_staff_phone_list()),
    ]
    for name, df in writes:
        df.to_excel(OUT_DIR / name, index=False, engine="openpyxl")
        print(f"Wrote {df.shape[0]:3d} rows -> {OUT_DIR / name}")

    GT_MAPPING_FILE.write_text(json.dumps(_ground_truth_mapping(), indent=2),
                               encoding="utf-8")
    n_cases = len({e["case_id"] for e in jan_may + summer + operational})
    payload = {"generated_at": datetime.now().isoformat(), "seed": SEED,
               "as_of": AS_OF.isoformat(), "cases": n_cases,
               "bottlenecks": gt, "operational_intent": op_intent}
    GT_BOTTLENECKS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Ground truth: {GT_MAPPING_FILE.name}, {GT_BOTTLENECKS_FILE.name}")
    print(f"Seeded over {n_cases} cases: {len(gt['delay'])} delay, "
          f"{len(gt['repetition'])} repetition, {len(gt['rework'])} rework")
    parked = {k: len(v) for k, v in op_intent["parked_at"].items()}
    print(f"Parked bookings by stage: {parked}  "
          f"unowned: {len(op_intent['unowned'])}")


if __name__ == "__main__":
    main()
