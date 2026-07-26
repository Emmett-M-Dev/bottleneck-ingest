"""Generate the messy Northstar Advisory drive — SME #3, professional services.

Northstar Advisory is a fictional 16-person consultancy running its whole
lead-to-cash workflow on spreadsheets:

    Lead -> Qualification -> Proposal -> Won -> Onboarding -> Delivery
         -> Client Review -> Invoice -> Paid

The drive is deliberately sedimented, in the ways a real one is:

    leads.xlsx                  the sales sheet (Lead Ref/Stage/Date/Owner/
                                Status/Est. Value) — early-stage engagements
    projects.xlsx               the delivery sheet, RENAMED headers (Project
                                Code/Milestone/Updated/Consultant/Notes/Fee (£))
                                — the same engagement refs, later stages
    projects - final NEW.xlsx   somebody's "final" copy of the delivery sheet:
                                overlapping rows plus a handful of newer ones
    clients.xlsx                reference — who the clients are
    timesheets.xlsx             reference — hours booked per consultant
    team_capacity.xlsx          reference — who can do what, and how much
    invoices.xlsx               reference — a wide sheet with date columns, no
                                event structure the pipeline can use directly

An engagement carries ONE reference across both event sheets, which is exactly
how these firms work — sales hands over to delivery and everyone keeps typing
the same code. The mapped reader's dedup absorbs the overlapping copy.

STRUCTURAL bottlenecks (recovered by the dynamic detector, no marker config):

    delay       proposals sit waiting for internal partner approval
    repetition  Client Review entered twice with no progress in between
    rework      work drops BACK to Delivery after a review — scope reopened

OPERATIONAL patterns (recovered by the generic case rules in
detection/case_rules.py). The generator records only its INTENT — which
engagement it parked where, and how stale it left it. Whether that breaches an
SLA is the detector's judgement, made from the data alone, so the
injection/detection separation that guards against circularity is preserved:

    uncontacted leads, proposals past internal approval, won work not
    onboarded, delivered work not invoiced, overdue invoices, unowned
    engagements, one overloaded consultant, one specialist everything queues
    behind

Everything is synthetic — the names, the clients and the money are invented.
Run:  python synthetic/generate_messy_advisory.py
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

SEED = 29
PROFILE = config.MESSY_PROFILES["advisory"]
OUT_DIR: Path = PROFILE["dir"]
GT_MAPPING_FILE: Path = PROFILE["gt_mapping"]
GT_BOTTLENECKS_FILE: Path = PROFILE["gt_bottlenecks"]

# The drive's "today" — the newest event in the log. The case rules age
# everything against it, so it is the one date the whole demo hangs off.
AS_OF = datetime(2026, 7, 20)

LEAD_HEADERS = ["Lead Ref", "Stage", "Date", "Owner", "Status", "Est. Value"]
PROJECT_HEADERS = ["Project Code", "Milestone", "Updated", "Consultant",
                   "Notes", "Fee (£)"]

# Stages live on one sheet or the other — sales hands over at Won.
SALES_STAGES = ["Lead", "Qualification", "Proposal", "Won"]
DELIVERY_STAGES = ["Onboarding", "Delivery", "Client Review", "Invoice", "Paid"]

SALES_OWNERS = ["Ruairi Doherty", "Meabh Kelly"]
CONSULTANTS = ["Aoife Brennan", "Declan Moore", "Sinead Yao", "Tom Whyte"]
# The specialist everything queues behind. Deliberately over-represented at the
# two stages that need her diagnostics expertise, so the key-person rule has
# something true to find.
SPECIALIST = "Aoife Brennan"
# Onboarding, invoicing and payment chasing are the practice manager's job in a
# firm this size. She does all of it, which is why those stages produce no
# key-person finding — a single-handed stage with nobody to compare against is
# not evidence of a dependency, and the rule requires at least two actors.
PRACTICE_MANAGER = "Niamh Foy"

DONE_ISH = ["done", "Done ", "complete", "Complete ✔", "sent", "ok"]
OPEN_ISH = ["with client", "awaiting partner sign-off", "", "tbc", "chasing"]

CLIENT_NAMES = [
    "Rowan Group", "Hillsborough Foods", "Cavanagh Print", "Lagan Logistics",
    "Ardmore Care", "Belfast Biotech", "Strand Retail", "Ulster Fabrication",
    "Portrush Marine", "Erne Energy", "Bann Financial", "Craigavon Plastics",
]


def _mess(stage: str) -> str:
    """Staff type stage names inconsistently. The canonicalisation upstream
    has to survive it — so seed it."""
    return random.choice([stage, stage.upper(), stage.lower(), stage + " "])


def _day(base: datetime, n: int) -> datetime:
    return base + timedelta(days=n)


class Engagement:
    """One client engagement, built stage by stage.

    `park_at` truncates the flow so the engagement is left sitting at that
    stage — the operational patterns are all "somebody stopped here and nobody
    came back", which is what actually happens in an SME."""

    def __init__(self, cid: str, client: str, start: datetime, value: int) -> None:
        self.cid = cid
        self.client = client
        self.value = value
        self.events: list[dict] = []
        self._t = start

    def add(self, stage: str, *, actor: str, status: str | None = None,
            gap: int = 0) -> None:
        self._t = _day(self._t, gap)
        self.events.append({
            "case_id": self.cid, "activity": _mess(stage), "ts": self._t,
            "actor": actor, "status": status or random.choice(DONE_ISH),
            "value": self.value, "client": self.client,
        })

    @property
    def last_ts(self) -> datetime:
        return self.events[-1]["ts"]


def build_engagement(cid: str, client: str, start: datetime, value: int, *,
                     delay: bool = False, repetition: bool = False,
                     rework: bool = False, park_at: str | None = None,
                     unowned: bool = False,
                     specialist: bool = False) -> Engagement:
    """Walk an engagement through the workflow, stopping at `park_at`."""
    eng = Engagement(cid, client, start, value)
    sales_owner = random.choice(SALES_OWNERS)
    consultant = SPECIALIST if specialist else random.choice(CONSULTANTS)

    def stop(stage: str) -> bool:
        return park_at == stage

    eng.add("Lead", actor="" if unowned else sales_owner, gap=0,
            status=random.choice(OPEN_ISH) if park_at == "Lead" else None)
    if stop("Lead"):
        return eng

    eng.add("Qualification", actor="" if unowned else sales_owner,
            gap=random.randint(1, 4))
    if stop("Qualification"):
        return eng

    # The delay signal: the proposal waits on internal partner approval. 16-24
    # days sits well clear of the log's own outlier threshold (normal gaps
    # top out around 6 days).
    eng.add("Proposal", actor="" if unowned else sales_owner,
            gap=random.randint(16, 24) if delay else random.randint(2, 6),
            status="awaiting partner sign-off" if park_at == "Proposal" else None)
    if stop("Proposal"):
        return eng

    eng.add("Won", actor="" if unowned else sales_owner, gap=random.randint(1, 5))
    if stop("Won"):
        return eng

    eng.add("Onboarding", actor=PRACTICE_MANAGER, gap=random.randint(1, 5))
    if stop("Onboarding"):
        return eng

    eng.add("Delivery", actor=consultant, gap=random.randint(2, 6))
    if stop("Delivery"):
        return eng

    eng.add("Client Review", actor=consultant, gap=random.randint(2, 6))
    if repetition:
        # The review went round again without the work moving on — a literal
        # second occurrence of the same stage, not a loop back.
        eng.add("Client Review", actor=consultant, gap=random.randint(2, 5),
                status=random.choice(OPEN_ISH))
    if rework:
        # Scope reopened after the review: a genuine BACKWARD transition. The
        # engagement goes straight to Invoice afterwards, so Client Review is
        # never entered a second time — that would double-report as repetition.
        eng.add("Delivery", actor=consultant, gap=random.randint(3, 6),
                status=random.choice(OPEN_ISH))
    if stop("Client Review"):
        return eng

    eng.add("Invoice", actor=PRACTICE_MANAGER, gap=random.randint(1, 5))
    if stop("Invoice"):
        return eng

    eng.add("Paid", actor=PRACTICE_MANAGER, gap=random.randint(3, 6))
    return eng


# ── The engagement plan ──────────────────────────────────────────────────────
# Each row: (offset weeks from the start, park_at, structural flags, notes).
# Written as data rather than code so the seeded story is readable at a glance.

_PLAN: list[dict] = [
    # Completed work — the healthy baseline the patterns stand out against.
    # Most delivery runs through the principal consultant, because operations
    # diagnostics is her specialism: that concentration is itself one of the
    # findings the case rules are meant to surface.
    {"weeks": 0,  "park": None, "specialist": True},
    {"weeks": 1,  "park": None, "specialist": True},
    {"weeks": 2,  "park": None},
    {"weeks": 3,  "park": None, "specialist": True},
    # Structural bottlenecks (>=2 cases each — the detector's min_affected).
    {"weeks": 4,  "park": None, "delay": True, "specialist": True},
    {"weeks": 5,  "park": None, "delay": True},
    {"weeks": 6,  "park": None, "delay": True, "specialist": True},
    {"weeks": 7,  "park": None, "repetition": True, "specialist": True},
    {"weeks": 8,  "park": None, "repetition": True},
    {"weeks": 9,  "park": None, "rework": True, "specialist": True},
    {"weeks": 10, "park": None, "rework": True, "specialist": True},
    # Operational patterns — engagements parked and left.
    {"weeks": 20, "park": "Lead"},
    {"weeks": 20, "park": "Lead"},
    {"weeks": 21, "park": "Lead", "unowned": True},
    {"weeks": 19, "park": "Proposal"},
    {"weeks": 19, "park": "Proposal"},
    {"weeks": 20, "park": "Won"},
    {"weeks": 20, "park": "Won", "unowned": True},
    {"weeks": 17, "park": "Client Review", "specialist": True},
    {"weeks": 17, "park": "Client Review", "specialist": True},
    {"weeks": 13, "park": "Invoice", "specialist": True},
    {"weeks": 12, "park": "Invoice", "specialist": True},
    {"weeks": 18, "park": "Delivery", "specialist": True},
    {"weeks": 18, "park": "Onboarding", "specialist": True},
    # Live delivery work, all of it sitting with the same principal consultant.
    # This is the concurrency that produces the overloaded-owner finding.
    {"weeks": 16, "park": "Delivery", "specialist": True},
    {"weeks": 15, "park": "Delivery", "specialist": True},
    {"weeks": 14, "park": "Client Review", "specialist": True},
]

_VALUES = [12_000, 18_500, 9_400, 26_000, 15_750, 31_200, 7_800, 22_400,
           13_600, 19_900, 28_500, 11_200, 16_800, 24_300, 8_900, 33_100,
           14_500, 20_700, 10_300, 27_600, 17_200, 21_900, 12_800, 29_400]

# The SAME drive a fortnight later, after the firm worked its action queue.
# Plan index -> where that engagement has got to now (None = it finished).
# This is what makes the outcome loop demonstrable: re-analysing this folder
# produces a second snapshot the interventions can genuinely be measured
# against, instead of comparing a baseline with itself.
FOLLOW_UP_WEEKS = 2
FOLLOW_UP_MOVES: dict[int, str | None] = {
    11: "Qualification",   # leads that were finally contacted
    12: "Qualification",
    13: "Qualification",
    14: "Won",             # proposals that got their partner sign-off
    15: "Won",
    16: "Onboarding",      # won work that got onboarded
    17: "Onboarding",
    18: "Invoice",         # delivered work that finally got billed
    19: "Invoice",
    20: None,              # overdue invoices that got paid
    21: None,
}


def build_events(follow_up: bool = False) -> tuple[list[Engagement], dict, dict]:
    """Every engagement, plus the structural and operational ground truth."""
    structural = {"delay": [], "repetition": [], "rework": []}
    operational: dict[str, list] = {
        "parked_at": {}, "unowned": [], "specialist_led": []}

    as_of = AS_OF + timedelta(weeks=FOLLOW_UP_WEEKS) if follow_up else AS_OF
    # The plan is anchored so that the most recently touched engagement lands
    # exactly on the drive's "today".
    base = as_of - timedelta(weeks=max(p["weeks"] for p in _PLAN) + 6)

    engagements: list[Engagement] = []
    for i, plan in enumerate(_PLAN):
        cid = f"NA-{1041 + i}"
        client = CLIENT_NAMES[i % len(CLIENT_NAMES)]
        park = plan.get("park")
        unowned = plan.get("unowned", False)
        if follow_up and i in FOLLOW_UP_MOVES:
            park = FOLLOW_UP_MOVES[i]
            unowned = False        # the queue said assign an owner, so they did
        eng = build_engagement(
            cid, client, base + timedelta(weeks=plan["weeks"]),
            _VALUES[i % len(_VALUES)],
            delay=plan.get("delay", False),
            repetition=plan.get("repetition", False),
            rework=plan.get("rework", False),
            park_at=park, unowned=unowned,
            specialist=plan.get("specialist", False))
        engagements.append(eng)
        plan = {**plan, "park": park, "unowned": unowned}

        for kind in ("delay", "repetition", "rework"):
            if plan.get(kind):
                structural[kind].append(cid)
        if plan.get("park"):
            operational["parked_at"].setdefault(plan["park"], []).append(cid)
        if plan.get("unowned"):
            operational["unowned"].append(cid)
        if plan.get("specialist"):
            operational["specialist_led"].append(cid)

    # Pull the whole drive forward so the newest event is exactly the "today"
    # this snapshot represents.
    newest = max(e.last_ts for e in engagements)
    shift = as_of - newest
    for eng in engagements:
        for ev in eng.events:
            ev["ts"] = ev["ts"] + shift

    for kind in structural:
        structural[kind].sort()
    operational["unowned"].sort()
    operational["specialist_led"].sort()
    for stage in operational["parked_at"]:
        operational["parked_at"][stage].sort()
    return engagements, structural, operational


# ── Sheets ───────────────────────────────────────────────────────────────────

def _split(engagements: list[Engagement]) -> tuple[list[dict], list[dict]]:
    sales, delivery = [], []
    for eng in engagements:
        for ev in eng.events:
            stage = ev["activity"].strip().title()
            (sales if stage in SALES_STAGES else delivery).append(ev)
    return sales, delivery


def _lead_frame(events: list[dict]) -> pd.DataFrame:
    rows = [{"Lead Ref": e["case_id"], "Stage": e["activity"],
             "Date": e["ts"].strftime("%d/%m/%Y"), "Owner": e["actor"],
             "Status": e["status"], "Est. Value": f"£{e['value']:,}"}
            for e in events]
    return pd.DataFrame(rows, columns=LEAD_HEADERS)


def _project_frame(events: list[dict]) -> pd.DataFrame:
    rows = [{"Project Code": e["case_id"], "Milestone": e["activity"],
             "Updated": e["ts"].strftime("%Y-%m-%d"), "Consultant": e["actor"],
             "Notes": e["status"], "Fee (£)": e["value"]}
            for e in events]
    return pd.DataFrame(rows, columns=PROJECT_HEADERS)


def build_clients(engagements: list[Engagement]) -> pd.DataFrame:
    sectors = ["Manufacturing", "Food & drink", "Logistics", "Healthcare",
               "Technology", "Retail", "Energy", "Financial services"]
    seen: dict[str, dict] = {}
    for i, eng in enumerate(engagements):
        seen.setdefault(eng.client, {
            "Client": eng.client, "Sector": sectors[i % len(sectors)],
            "Account Manager": SALES_OWNERS[i % len(SALES_OWNERS)],
            "Retainer": random.choice(["yes", "no", "no", ""]),
        })
    return pd.DataFrame(list(seen.values()))


def build_timesheets(engagements: list[Engagement]) -> pd.DataFrame:
    rows = []
    for eng in engagements[:14]:
        consultant = next((e["actor"] for e in reversed(eng.events)
                           if e["actor"] in CONSULTANTS), CONSULTANTS[0])
        for week in range(1, random.randint(2, 4)):
            rows.append({
                "Consultant": consultant, "Project Code": eng.cid,
                "Week Ending": (eng.last_ts - timedelta(weeks=week)).strftime("%d/%m/%Y"),
                "Hours": random.choice([7.5, 15, 22.5, 30, 37.5]),
                "Billable": random.choice(["Y", "y", "yes", "N"]),
            })
    return pd.DataFrame(rows)


def build_team_capacity() -> pd.DataFrame:
    rows = [
        {"Consultant": "Aoife Brennan", "Role": "Principal consultant",
         "Weekly Hours": 37.5, "Specialism": "Operations diagnostics"},
        {"Consultant": "Declan Moore", "Role": "Senior consultant",
         "Weekly Hours": 37.5, "Specialism": "Finance transformation"},
        {"Consultant": "Sinead Yao", "Role": "Consultant",
         "Weekly Hours": 30, "Specialism": "Data & reporting"},
        {"Consultant": "Tom Whyte", "Role": "Analyst",
         "Weekly Hours": 37.5, "Specialism": "Research"},
        {"Consultant": "Ruairi Doherty", "Role": "Business development",
         "Weekly Hours": 37.5, "Specialism": ""},
        {"Consultant": "Meabh Kelly", "Role": "Business development",
         "Weekly Hours": 22.5, "Specialism": "Public sector"},
        {"Consultant": "Niamh Foy", "Role": "Practice manager",
         "Weekly Hours": 37.5, "Specialism": "Onboarding & billing"},
    ]
    return pd.DataFrame(rows)


def build_invoices(engagements: list[Engagement]) -> pd.DataFrame:
    """A wide sheet with three date columns and no single event column — the
    kind of file that looks like an event log and is not one."""
    rows = []
    for i, eng in enumerate(engagements):
        stages = {e["activity"].strip().title() for e in eng.events}
        if "Invoice" not in stages:
            continue
        issued = next(e["ts"] for e in eng.events
                      if e["activity"].strip().title() == "Invoice")
        paid = next((e["ts"] for e in eng.events
                     if e["activity"].strip().title() == "Paid"), None)
        rows.append({
            "Invoice No": f"INV-{2600 + i}", "Project Code": eng.cid,
            "Client": eng.client, "Net": eng.value,
            "Issued": issued.strftime("%d/%m/%Y"),
            "Due": (issued + timedelta(days=30)).strftime("%d/%m/%Y"),
            "Paid On": paid.strftime("%d/%m/%Y") if paid else "",
        })
    return pd.DataFrame(rows)


def _ground_truth_mapping() -> dict:
    lead_fields = ["case_id", "activity", "timestamp", "actor", "status", "value"]
    project_fields = ["case_id", "activity", "timestamp", "actor", "status", "value"]
    return {
        "profile": "advisory",
        "seed": SEED,
        "files": [
            {"filename": "leads.xlsx", "sheet": "Sheet1", "role": "events",
             "include": True, "columns": dict(zip(LEAD_HEADERS, lead_fields))},
            {"filename": "projects.xlsx", "sheet": "Sheet1", "role": "events",
             "include": True,
             "columns": dict(zip(PROJECT_HEADERS, project_fields))},
            {"filename": "projects - final NEW.xlsx", "sheet": "Sheet1",
             "role": "events", "include": True,
             "columns": dict(zip(PROJECT_HEADERS, project_fields))},
            {"filename": "clients.xlsx", "sheet": "Sheet1", "role": "reference",
             "include": True, "columns": {}},
            {"filename": "timesheets.xlsx", "sheet": "Sheet1", "role": "reference",
             "include": True, "columns": {}},
            {"filename": "team_capacity.xlsx", "sheet": "Sheet1",
             "role": "reference", "include": True, "columns": {}},
            {"filename": "invoices.xlsx", "sheet": "Sheet1", "role": "reference",
             "include": True, "columns": {}},
        ],
        "expected_issues": {
            "duplicates": ["'projects - final NEW.xlsx' repeats rows of "
                           "projects.xlsx under the same project codes"],
            "overlaps": ["projects.xlsx and 'projects - final NEW.xlsx' both "
                         "cover the same delivery milestones",
                         "leads.xlsx and projects.xlsx share engagement "
                         "references across the sales/delivery handover"],
            "gaps": ["invoices.xlsx has three date columns and no single "
                     "activity column — it cannot drive an event log"],
            "irrelevant": [],
        },
    }


def follow_up_dir() -> Path:
    return OUT_DIR.parent / f"{OUT_DIR.name}_followup"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the Northstar Advisory messy drive")
    parser.add_argument("--follow-up", action="store_true",
                        help=(f"write the same drive {FOLLOW_UP_WEEKS} weeks "
                              f"later, after the action queue was worked — the "
                              f"second snapshot outcomes are measured against"))
    args = parser.parse_args()

    random.seed(SEED)
    out_dir = follow_up_dir() if args.follow_up else OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    engagements, structural, operational = build_events(follow_up=args.follow_up)
    sales_events, delivery_events = _split(engagements)

    # Somebody saved a "final" copy of the delivery sheet mid-quarter: the
    # older rows are duplicated verbatim, and a few newer ones exist only there.
    copied = delivery_events[:14]
    only_here = delivery_events[-6:]
    fork_rows = copied + only_here

    writes = [
        ("leads.xlsx", _lead_frame(sales_events)),
        ("projects.xlsx", _project_frame(delivery_events)),
        ("projects - final NEW.xlsx", _project_frame(fork_rows)),
        ("clients.xlsx", build_clients(engagements)),
        ("timesheets.xlsx", build_timesheets(engagements)),
        ("team_capacity.xlsx", build_team_capacity()),
        ("invoices.xlsx", build_invoices(engagements)),
    ]
    for name, df in writes:
        df.to_excel(out_dir / name, index=False, engine="openpyxl")
        print(f"Wrote {df.shape[0]:3d} rows -> {out_dir / name}")

    if args.follow_up:
        # The follow-up snapshot reuses the primary drive's mapping and ground
        # truth — same schema, same seeded structure, a fortnight on.
        print(f"\nFollow-up snapshot written. Re-analyse it with:\n"
              f"  python ingest.py --source messy --profile advisory "
              f"--drive {out_dir.relative_to(config.ROOT)}")
        return

    GT_MAPPING_FILE.write_text(json.dumps(_ground_truth_mapping(), indent=2),
                               encoding="utf-8")
    payload = {
        "generated_at": datetime.now().isoformat(),
        "seed": SEED,
        "as_of": AS_OF.isoformat(),
        "follow_up_weeks": FOLLOW_UP_WEEKS,
        "cases": len(engagements),
        "bottlenecks": structural,
        # The generator's INTENT for the operational patterns. The case rules
        # decide independently whether these breach anything — that separation
        # is what keeps the eval honest.
        "operational_intent": operational,
        "case_values": {e.cid: e.value for e in engagements},
    }
    GT_BOTTLENECKS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\nGround truth: {GT_MAPPING_FILE.name}, {GT_BOTTLENECKS_FILE.name}")
    print(f"Seeded over {len(engagements)} engagements: "
          f"{len(structural['delay'])} delay, {len(structural['repetition'])} "
          f"repetition, {len(structural['rework'])} rework")
    parked = {k: len(v) for k, v in operational["parked_at"].items()}
    print(f"Parked engagements by stage: {parked}")
    print(f"Unowned: {len(operational['unowned'])}  "
          f"Specialist-led: {len(operational['specialist_led'])}")


if __name__ == "__main__":
    main()
