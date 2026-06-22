"""Generate the multi-sheet Foyle operations dataset (Phase A).

Models how a small placement office actually runs: three inbound emails arrive,
and staff transcribe the same information by hand into six separate spreadsheets.
That manual fan-out is where the bottlenecks live — the same student is re-keyed
into every sheet, payments lag behind invoices, and placements get re-allocated
when a host drops out or a company declines.

Outputs (data/synthetic/foyle/):
    emails/email_1_group_request.txt     partner sends a cohort of students
    emails/email_2_host_update.txt       host coordinator confirms + one drop-out
    emails/email_3_company_replies.txt   placement companies confirm / decline
    placements.xlsx        master roster      (one row per student)
    host_families.xlsx     homestay tracker   (one row per hosting; drop-outs flip status)
    work_placements.xlsx   internship tracker (declines -> re-allocation)
    documents.xlsx         CV/ML/consent tracker (re-requests = repetition)
    invoices.xlsx          invoice -> payment  (gap = delay)
    accessni.xlsx          background checks
    ground_truth_foyle.json   the seeded answers (never read by any detector)

EVERYTHING is synthetic. Student/host/mentor names, partner agencies, placement
orgs, emails and phone numbers are fabricated. The schema is modelled on the real
"DERRY-DONEGAL Placements by Month" workbook only in structure, never its data.

This is a stand-alone data builder: it shares no imports with the existing
event-log pipeline and does not touch bookings_tracker.xlsx. Wiring these sheets
into ingest/detection is Phase B.

Run:  python synthetic/generate_foyle.py
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

SEED = 7
OUT_DIR = config.DATA_SYNTHETIC / "foyle"

# ── Synthetic vocabulary (all fabricated) ────────────────────────────────────
PARTNER = "EduMobil Bremen"          # the sending agency for this cohort
PARTNER_CONTACT = "Frau Kathrin Vogel"
PARTNER_EMAIL = "k.vogel@edumobil-bremen.example"
PARTNER_PHONE = "+49 421 555 0142"

ARRIVAL = datetime(2026, 4, 7)       # whole cohort arrives together
DEPARTURE = datetime(2026, 5, 4)

GERMAN_FIRST = [
    "Lena", "Marie", "Hannah", "Lukas", "Jonas", "Felix", "Mia", "Emma",
    "Paul", "Leon", "Clara", "Greta", "Finn", "Noah", "Lara", "Tobias",
]
GERMAN_LAST = [
    "Becker", "Hoffmann", "Schaefer", "Wagner", "Richter", "Klein", "Wolf",
    "Neumann", "Schwarz", "Braun", "Krueger", "Lehmann", "Vogel", "Frank",
    "Berg", "Sommer",
]

# Host families (fabricated Derry/Donegal names) with their area + postcode.
HOSTS = [
    ("Bernadette Coyle", "Bogside, Derry", "BT48 9AX"),
    ("Marie Gallagher", "Culmore, Derry", "BT48 8JB"),
    ("Sean Doherty", "Waterside, Derry", "BT47 2AB"),
    ("Kathleen Friel", "Creggan, Derry", "BT48 9QE"),
    ("Brendan McLaughlin", "Buncrana, Donegal", "F93 K2X1"),
    ("Aoife Bradley", "Rosemount, Derry", "BT48 0EU"),
    ("Declan Harkin", "Strathfoyle, Derry", "BT47 6TG"),
    ("Noreen Sweeney", "Carndonagh, Donegal", "F93 W8Y4"),
]

# Placement organisations keyed by sector (fabricated, NW-Ireland flavoured).
ORGS = {
    "Education":   ["Riverside Primary School", "St Brigid's PS", "Foyleside Primary"],
    "Early Years": ["Rainbow Day Nursery", "Little Acorns Nursery", "Wee Folk Playgroup"],
    "Healthcare":  ["Bogside Care Home", "Sevenview Nursing Home", "Deanview Residential"],
    "Hospitality": ["Walled City Hotel", "Harbour View Inn", "An Bradan Restaurant"],
    "IT":          ["Northwest Tech Hub", "Foyle Digital Studio", "Bytewise Solutions"],
}
SECTORS = list(ORGS.keys())

MENTORS = [
    "Joy McCallion", "Thelma Boyle", "John Coyle", "Karen Brennan",
    "Margaret Doherty", "Ciaran Friel",
]
# Foyle staff who maintain the sheets (appear in an "Updated By" column).
STAFF = ["Aine Murray", "Paul Doherty", "Niamh Kelly", "Orla McLaughlin", "Sean Gallagher"]

# The host who drops out (email 2) and the company that declines (email 3).
DROP_HOST = "Bernadette Coyle"
DECLINE_COMPANY = "Walled City Hotel"


# ── Messiness helpers (staff-entered sheets are never tidy) ───────────────────
def _date(dt: datetime, style: int | None = None) -> str:
    fmts = ["%d.%m.%y", "%d/%m/%Y", "%d-%b-%Y", "%d.%m.%Y", "%Y-%m-%d"]
    return dt.strftime(fmts[style if style is not None else random.randrange(len(fmts))])


def _yes() -> str:
    return random.choice(["Yes", "yes", "y", "Y", "done", "received"])


def _maybe(value, p_blank=0.1):
    return "" if random.random() < p_blank else value


def _wobble_name(name: str) -> str:
    """Occasionally re-key a name slightly differently across sheets (the real
    transcription error that makes cross-sheet matching hard)."""
    if random.random() < 0.18:
        if " " in name and random.random() < 0.5:
            first, last = name.split(" ", 1)
            return f"{last}, {first}"          # surname-first in one sheet
        return name.replace("ae", "ä").replace("ue", "ü").replace("oe", "ö")
    return name


# ── Build the canonical cohort (the single source of truth) ───────────────────
def build_cohort(n: int = 14) -> list[dict]:
    students: list[dict] = []
    firsts = random.sample(GERMAN_FIRST, n)
    lasts = random.sample(GERMAN_LAST, n)
    for i in range(n):
        sector = random.choice(SECTORS)
        prefs = random.sample(ORGS[sector], k=min(3, len(ORGS[sector])))
        potential = prefs[0]
        # rework: ~25% are re-allocated away from their potential placement
        reworked = random.random() < 0.25
        confirmed = prefs[1] if (reworked and len(prefs) > 1) else potential

        host = random.choice(HOSTS)
        invoice_date = ARRIVAL - timedelta(days=random.randint(20, 45))
        # delay: ~35% pay late (or not at all yet)
        late = random.random() < 0.35
        pay_lag = random.randint(25, 55) if late else random.randint(2, 14)
        pending = late and random.random() < 0.4

        students.append({
            "sid": f"S{i+1:02d}",
            "first": firsts[i],
            "last": lasts[i],
            "name": f"{firsts[i]} {lasts[i]}",
            "age": random.randint(16, 23),
            "gender": random.choice(["F", "M"]),
            "sector": sector,
            "pref1": prefs[0],
            "pref2": prefs[1] if len(prefs) > 1 else "",
            "pref3": prefs[2] if len(prefs) > 2 else "",
            "potential": potential,
            "confirmed": confirmed,
            "reworked": reworked,
            "host": host,                       # (name, area, postcode)
            "mentor": random.choice(MENTORS),
            "is_internship": sector in ("Hospitality", "IT"),
            "invoice_no": f"INV-{2600 + i}",
            "invoice_date": invoice_date,
            "amount": random.choice([1450, 1620, 1780, 1950, 2100]),
            "pay_lag": pay_lag,
            "pending": pending,
            "doc_rerequest": random.random() < 0.4,   # repetition: docs chased twice
        })
    return students


# ── Render each sheet as a wide, staff-style roster ──────────────────────────
def sheet_placements(cohort) -> pd.DataFrame:
    rows = []
    for s in cohort:
        rows.append({
            "Partner": PARTNER,
            "Student Name": _wobble_name(s["name"]),
            "Age": s["age"],
            "Gender": s["gender"],
            "Arrival": _date(ARRIVAL),
            "Departure": _date(DEPARTURE),
            "Duration": "4 weeks",
            "Location": "Derry",
            "Sector": s["sector"],
            "1st Preference": s["pref1"],
            "2nd Preference": s["pref2"],
            "3rd Preference": s["pref3"],
            "Potential Placement": s["potential"],
            "Confirmed Placement": _maybe(s["confirmed"], 0.15),
            "Accommodation": s["host"][0],
            "Mentor": _maybe(s["mentor"], 0.2),
            "Welcome Letter": _maybe(_yes(), 0.3),
            "Notes": "re-allocated from 1st pref" if s["reworked"] else "",
            "Updated By": random.choice(STAFF),
        })
    return pd.DataFrame(rows)


def sheet_host_families(cohort) -> pd.DataFrame:
    rows = []
    for s in cohort:
        host, area, postcode = s["host"]
        dropped = host == DROP_HOST
        rows.append({
            "Host Family": host,
            "Area": area,
            "Postcode": postcode,
            "Capacity": random.randint(1, 3),
            "Student Hosted": _wobble_name(s["name"]),   # re-keyed -> repetition
            "Partner": PARTNER,
            "Arrival": _date(ARRIVAL),
            "Departure": _date(DEPARTURE),
            "Status": "DROPPED OUT" if dropped else random.choice(["Confirmed", "confirmed", "ok"]),
            "Contact": f"07700 9{random.randint(10000, 99999)}",
            "Updated By": random.choice(STAFF),
        })
        # rework: the dropped host's students get re-housed in a second row
        if dropped:
            new_host, new_area, new_pc = random.choice([h for h in HOSTS if h[0] != DROP_HOST])
            rows.append({
                "Host Family": new_host,
                "Area": new_area,
                "Postcode": new_pc,
                "Capacity": random.randint(1, 3),
                "Student Hosted": s["name"],
                "Partner": PARTNER,
                "Arrival": _date(ARRIVAL),
                "Departure": _date(DEPARTURE),
                "Status": "Re-housed",
                "Contact": f"07700 9{random.randint(10000, 99999)}",
                "Updated By": random.choice(STAFF),
            })
    return pd.DataFrame(rows)


def sheet_work_placements(cohort) -> pd.DataFrame:
    rows = []
    for s in cohort:
        if not s["is_internship"]:
            continue
        declined = s["confirmed"] == DECLINE_COMPANY or (
            s["pref1"] == DECLINE_COMPANY and random.random() < 0.6
        )
        company = s["confirmed"]
        if declined:
            company = s["pref2"] or s["confirmed"]
        rows.append({
            "Company": s["pref1"],
            "Sector": s["sector"],
            "Student Name": _wobble_name(s["name"]),
            "Mentor": s["mentor"],
            "Start Date": _date(ARRIVAL + timedelta(days=2)),
            "End Date": _date(DEPARTURE - timedelta(days=2)),
            "Confirmed?": "DECLINED" if declined else _yes(),
            "Re-allocated To": company if declined else "",
            "Contact": f"028 71 {random.randint(100000, 999999)}",
            "Updated By": random.choice(STAFF),
        })
    return pd.DataFrame(rows)


def sheet_documents(cohort) -> pd.DataFrame:
    rows = []
    for s in cohort:
        requested = s["invoice_date"] + timedelta(days=random.randint(1, 6))
        received = requested + timedelta(days=random.randint(2, 20))
        # repetition: re-keyed name + (sometimes) age that disagrees with placements
        age = s["age"] + (1 if random.random() < 0.15 else 0)
        rows.append({
            "Student Name": _wobble_name(s["name"]),
            "Partner": PARTNER,
            "Age": age,
            "CV": _maybe(_yes(), 0.1),
            "Motivation Letter": _maybe(_yes(), 0.15),
            "Parental Consent": _maybe(_yes(), 0.2) if s["age"] < 18 else "N/A",
            "Enrolment Form": _maybe(_yes(), 0.1),
            "Welcome Letter": _maybe(_yes(), 0.3),
            "Date Requested": _date(requested),
            "Date Received": _maybe(_date(received), 0.15),
            "Re-requested?": "Yes - chased 2nd time" if s["doc_rerequest"] else "",
            "Updated By": random.choice(STAFF),
        })
    return pd.DataFrame(rows)


def sheet_invoices(cohort) -> pd.DataFrame:
    rows = []
    for s in cohort:
        inv = s["invoice_date"]
        pay = inv + timedelta(days=s["pay_lag"])
        rows.append({
            "Invoice No": s["invoice_no"],
            "Partner": PARTNER,
            "Student Name": _wobble_name(s["name"]),
            "Invoice Date": _date(inv),
            "Amount (GBP)": s["amount"],
            "Payment Received": "PENDING" if s["pending"] else _yes(),
            "Payment Date": "" if s["pending"] else _date(pay),
            "Days to Pay": "" if s["pending"] else s["pay_lag"],
            "Updated By": random.choice(STAFF),
        })
    return pd.DataFrame(rows)


def sheet_accessni(cohort) -> pd.DataFrame:
    rows = []
    for s in cohort:
        submitted = s["invoice_date"] + timedelta(days=random.randint(3, 10))
        cleared = submitted + timedelta(days=random.randint(10, 35))
        outstanding = random.random() < 0.25
        rows.append({
            "Student Name": _wobble_name(s["name"]),
            "Sector": s["sector"],
            "Check Type": "Enhanced AccessNI" if s["sector"] in ("Education", "Early Years", "Healthcare") else "Basic AccessNI",
            "Submitted Date": _date(submitted),
            "Cleared Date": "" if outstanding else _date(cleared),
            "Status": "Awaiting clearance" if outstanding else "Cleared",
            "Updated By": random.choice(STAFF),
        })
    return pd.DataFrame(rows)


# ── The three driver emails ──────────────────────────────────────────────────
def emails(cohort) -> dict[str, str]:
    roster = "\n".join(
        f"  {i+1}. {s['name']}, age {s['age']}, {s['gender']} — {s['sector']} "
        f"(pref: {s['pref1']})"
        for i, s in enumerate(cohort)
    )
    drop_students = [s for s in cohort if s["host"][0] == DROP_HOST]
    drop_list = ", ".join(s["name"] for s in drop_students) or "(none this cohort)"
    decline_students = [
        s for s in cohort if s["is_internship"] and s["pref1"] == DECLINE_COMPANY
    ]
    decline_list = ", ".join(s["name"] for s in decline_students) or "(none this cohort)"

    return {
        "email_1_group_request.txt": (
            f"From: {PARTNER_CONTACT} <{PARTNER_EMAIL}>\n"
            f"To: bookings@foyle-mock-sme.example\n"
            f"Subject: {PARTNER} — April placement group ({len(cohort)} students)\n\n"
            f"Dear Foyle team,\n\n"
            f"Please find our April cohort below. All arrive {ARRIVAL:%d %B %Y} and "
            f"depart {DEPARTURE:%d %B %Y} (4 weeks, Derry). Could you confirm host "
            f"families and work placements, and let me know which CVs and consent "
            f"forms you still need.\n\n"
            f"{roster}\n\n"
            f"Invoices can go to me directly. Reachable on {PARTNER_PHONE}.\n\n"
            f"Beste Gruesse,\n{PARTNER_CONTACT}\n{PARTNER}"
        ),
        "email_2_host_update.txt": (
            f"From: Niamh Kelly <hosts@foyle-mock-sme.example>\n"
            f"To: bookings@foyle-mock-sme.example\n"
            f"Subject: Host families for {PARTNER} April group — one drop-out\n\n"
            f"Hi all,\n\n"
            f"Host families are mostly confirmed for the April group. One problem: "
            f"{DROP_HOST} has had to pull out this week for family reasons, so the "
            f"following student(s) need re-housing urgently: {drop_list}.\n\n"
            f"I've started re-allocating them to other hosts — please update the "
            f"placements sheet and the host families tracker so they match. We also "
            f"still don't have welcome letters out for several students.\n\n"
            f"Thanks,\nNiamh"
        ),
        "email_3_company_replies.txt": (
            f"From: placements@foyle-mock-sme.example\n"
            f"To: bookings@foyle-mock-sme.example\n"
            f"Subject: Work placement replies — {PARTNER} April group\n\n"
            f"Morning,\n\n"
            f"Updates from the companies:\n"
            f"- {DECLINE_COMPANY} can no longer take their intern(s) this month "
            f"({decline_list}) — we'll need to move them to their 2nd preference.\n"
            f"- A couple of the IT placements are asking us to re-send the students' "
            f"CVs, they didn't receive the first set.\n\n"
            f"Can someone re-request those CVs and re-allocate the hospitality "
            f"student(s)? Update the work placements sheet when done.\n\n"
            f"Cheers"
        ),
    }


def main() -> None:
    random.seed(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "emails").mkdir(exist_ok=True)

    cohort = build_cohort()

    sheets = {
        "placements.xlsx": sheet_placements(cohort),
        "host_families.xlsx": sheet_host_families(cohort),
        "work_placements.xlsx": sheet_work_placements(cohort),
        "documents.xlsx": sheet_documents(cohort),
        "invoices.xlsx": sheet_invoices(cohort),
        "accessni.xlsx": sheet_accessni(cohort),
    }
    for name, df in sheets.items():
        df.to_excel(OUT_DIR / name, index=False, engine="openpyxl")

    for name, body in emails(cohort).items():
        (OUT_DIR / "emails" / name).write_text(body, encoding="utf-8")

    # Ground truth — the seeded answers, for evaluating Phase B detection later.
    gt = {
        "generated_at": datetime.now().isoformat(),
        "seed": SEED,
        "partner": PARTNER,
        "cohort_size": len(cohort),
        "sheets": list(sheets.keys()),
        "bottlenecks": {
            "delay_invoice_to_payment": [
                s["invoice_no"] for s in cohort if s["pay_lag"] >= 21 or s["pending"]
            ],
            "repetition_rekeyed_students": [s["name"] for s in cohort],
            "rework_reallocated": [s["name"] for s in cohort if s["reworked"]],
            "host_dropouts": [s["name"] for s in cohort if s["host"][0] == DROP_HOST],
            "doc_rerequests": [s["name"] for s in cohort if s["doc_rerequest"]],
        },
    }
    (OUT_DIR / "ground_truth_foyle.json").write_text(json.dumps(gt, indent=2), encoding="utf-8")

    print(f"Wrote {len(sheets)} sheets + 3 emails to {OUT_DIR}")
    for name, df in sheets.items():
        print(f"  {name:22s} {df.shape[0]:3d} rows x {df.shape[1]} cols")
    bn = gt["bottlenecks"]
    print("Seeded:",
          f"{len(bn['delay_invoice_to_payment'])} late payments,",
          f"{len(bn['rework_reallocated'])} re-allocations,",
          f"{len(bn['doc_rerequests'])} doc re-requests,",
          f"{len(bn['host_dropouts'])} host drop-outs")


if __name__ == "__main__":
    main()
