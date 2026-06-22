"""Generate synthetic Foyle International (student-placement) operations data.

Domain: an English-language school placing international students with host families.
Each case is a student placement moving through request -> placement -> confirmation
-> invoice -> documents -> pre-arrival -> arrival. Produces a deliberately messy
event-log spreadsheet, a Sheet-ready CSV (for pasting into the live mock Google
Sheet), plain-text ops notes, and a ground-truth answer sheet recording three
injected bottlenecks.

All names, schools, host families, emails and phone numbers are synthetic — no real
Foyle student or staff data is used. The schema mirrors the real SMMP workflow only
in structure.

ISOLATION RULE (Phase 1 DoD): this module shares ZERO imports with any reader,
scrub, pipeline, or detection file. It imports only the standard library, pandas,
and `config` (for the output directory). The detector must rediscover the
bottlenecks from event_log.parquet alone — ground_truth.json is never read by the
pipeline.

Run:  python synthetic/generate.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Run as a plain script (python synthetic/generate.py): make the repo root importable
# so `import config` resolves. This is the only pipeline-external entry point.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

SEED = 42
N_CASES = 150
WINDOW_START = datetime(2026, 1, 1)
WINDOW_END = datetime(2026, 4, 1)  # placement requests arrive within Jan–Apr

STAGES = [
    "Request Received",
    "Placement Offer",
    "Booking Confirmed",
    "Invoice Issued",
    "Pre-Arrival Logistics",
    "Arrival",
]

# Base gap (days) leading INTO each stage from the previous one.
NORMAL_GAPS = {
    "Placement Offer": (1, 3),
    "Booking Confirmed": (1, 2),          # BN001 inflates this for ~60% of cases
    "Invoice Issued": (1, 4),
    "Pre-Arrival Logistics": (2, 6),
    "Arrival": (3, 10),
}

BN001_DELAY_GAP = (8, 14)   # Placement Offer -> Booking Confirmed, inflated (host-family matching)
BN001_RATE = 0.60
BN002_RATE = 0.40           # student documents (CV/ML/consent) re-requested / re-keyed
BN003_RATE = 0.30           # placement re-allocation loop-back (host fell through)

# Synthetic Foyle staff (Derry/Donegal-flavoured). Used for the actor field (masked
# downstream) and inside the ops notes for the scrubber to catch.
STAFF = [
    "Aine Murray", "Paul Doherty", "Niamh Kelly", "Sean Gallagher", "Orla McLaughlin",
    "Ciaran Bradley", "Roisin Friel", "Declan Quinn",
]

STATUSES = ["done", "complete", "completed", "ok", "closed"]


def _rand_date_in_window() -> datetime:
    span = (WINDOW_END - WINDOW_START).days
    return WINDOW_START + timedelta(days=random.randint(0, span),
                                    hours=random.randint(8, 17),
                                    minutes=random.choice([0, 15, 30, 45]))


def _messy_date(dt: datetime) -> str:
    """Render a datetime in one of several inconsistent formats (intentional mess)."""
    fmt = random.choice([
        "%Y-%m-%d",            # 2026-01-05
        "%d/%m/%Y",            # 05/01/2026
        "%b %d, %Y",           # Jan 05, 2026
        "%Y-%m-%d %H:%M",      # 2026-01-05 14:30
        "%d-%b-%Y",            # 05-Jan-2026
    ])
    return dt.strftime(fmt)


def _messy_case(label: str) -> str:
    """Inconsistent capitalisation + stray whitespace on activity labels."""
    style = random.random()
    if style < 0.15:
        label = label.lower()
    elif style < 0.25:
        label = label.upper()
    if random.random() < 0.12:
        label = "  " + label
    if random.random() < 0.12:
        label = label + " "
    return label


def _maybe_blank(value: str) -> str | None:
    return None if random.random() < 0.08 else value


def _build_case(idx: int) -> tuple[list[dict], dict]:
    """Return (event rows, injection flags) for one case."""
    case_id = f"FOY-{idx:04d}"
    inject_delay = random.random() < BN001_RATE
    inject_repeat = random.random() < BN002_RATE
    inject_rework = random.random() < BN003_RATE

    t = _rand_date_in_window()
    rows: list[dict] = []

    def emit(activity: str, when: datetime) -> None:
        rows.append({
            "Booking Ref": case_id,
            "Stage": _messy_case(activity),
            "Date": _messy_date(when),
            "Handled By": _maybe_blank(random.choice(STAFF)),
            "Status": _maybe_blank(random.choice(STATUSES)),
        })

    # Request Received — partner school sends the placement request
    emit("Request Received", t)

    # Placement Offer — host family + course offered
    t += timedelta(days=random.randint(*NORMAL_GAPS["Placement Offer"]))
    emit("Placement Offer", t)

    # BN003 — Placement Re-allocation loop-back (host fell through) before confirmation
    if inject_rework:
        t += timedelta(days=random.randint(1, 3))
        emit("Placement Re-allocation", t)

    # Booking Confirmed — BN001 inflates the gap (host-family matching backlog)
    gap = BN001_DELAY_GAP if inject_delay else NORMAL_GAPS["Booking Confirmed"]
    t += timedelta(days=random.randint(*gap))
    emit("Booking Confirmed", t)

    # Invoice Issued — invoice sent to the partner school
    t += timedelta(days=random.randint(*NORMAL_GAPS["Invoice Issued"]))
    emit("Invoice Issued", t)

    # BN002 — student documents (CV/ML/consent) re-requested and re-keyed
    if inject_repeat:
        t += timedelta(days=random.randint(0, 1))
        emit("Document Re-request", t)

    # Pre-Arrival Logistics
    t += timedelta(days=random.randint(*NORMAL_GAPS["Pre-Arrival Logistics"]))
    emit("Pre-Arrival Logistics", t)

    # Arrival
    t += timedelta(days=random.randint(*NORMAL_GAPS["Arrival"]))
    emit("Arrival", t)

    flags = {"delay": inject_delay, "repeat": inject_repeat, "rework": inject_rework}
    return rows, flags


OPS_NOTES = {
    "ops_notes_jan.txt": (
        "January was busy with placement requests from our German partner schools. "
        "Aine Murray at Lindenhof Gymnasium chased us twice about a placement "
        "confirmation that took nearly two weeks to come back while we matched a host "
        "family. You can reach her on aine.murray@foyle-mock-sme.example or "
        "07700 900123.\n\n"
        "The team keeps re-requesting the same student documents — CVs, motivation "
        "letters and parental consent forms — and re-typing the details into the "
        "homestay and enrolment sheets. It is slow and we have had a couple of "
        "mismatches. We should stop collecting the same paperwork twice."
    ),
    "ops_notes_feb.txt": (
        "February pain points: host placements are falling through and getting "
        "re-allocated too often. Paul Doherty re-placed the St. Brendan's group three "
        "times before the bookings were confirmed. Host families in BT48 7PT and "
        "beyond are dropping out late.\n\n"
        "Placement confirmations are still the slow step. Several students waited 8 to "
        "12 days for a confirmed host family. Parents email to ask whether the "
        "homestay is actually booked."
    ),
    "ops_notes_mar.txt": (
        "March review. The confirmation backlog has not improved. Niamh Kelly flagged "
        "that the same placement can sit for over a week before a host family is "
        "signed off.\n\n"
        "Document re-requests came up again at the standup — students re-sending CVs "
        "and consent forms that we already had on file. Contact the office on "
        "028 9012 3456 or ops@foyle-mock-sme.example if you spot a duplicate. We need "
        "a single source of truth for student paperwork."
    ),
}


def main() -> None:
    random.seed(SEED)
    out_dir: Path = config.DATA_SYNTHETIC
    out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    affected = {"delay": [], "repeat": [], "rework": []}

    for i in range(1, N_CASES + 1):
        rows, flags = _build_case(i)
        all_rows.extend(rows)
        case_id = f"BR-{i:04d}"
        for key, hit in flags.items():
            if hit:
                affected[key].append(case_id)

    df = pd.DataFrame(all_rows, columns=["Booking Ref", "Stage", "Date", "Handled By", "Status"])
    xlsx_path = out_dir / "bookings_tracker.xlsx"
    df.to_excel(xlsx_path, index=False, engine="openpyxl")

    # Sheet-ready CSV: paste-replace Sheet1 of the live mock Google Sheet
    # (foyle.mock.sme@gmail.com). Same columns/order as the Sheet the pipeline reads.
    csv_path = out_dir / "bookings_tracker_sheet.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8")

    for name, body in OPS_NOTES.items():
        (out_dir / name).write_text(body, encoding="utf-8")

    ground_truth = {
        "generated_at": datetime.now().isoformat(),
        "seed": SEED,
        "n_cases": N_CASES,
        "window": "2026-01 to 2026-04",
        "total_event_rows": len(all_rows),
        "bottlenecks": [
            {
                "id": "BN001",
                "type": "delay",
                "stage": "Booking Confirmed",
                "description": "Booking Confirmed (host-family matching) takes 8-14 days instead of 1-2.",
                "affected_cases": affected["delay"],
                "affected_count": len(affected["delay"]),
            },
            {
                "id": "BN002",
                "type": "repetition",
                "stage": "Document Re-request",
                "description": "Student documents (CV/ML/consent) re-requested and re-keyed (duplicate activity after Invoice Issued).",
                "affected_cases": affected["repeat"],
                "affected_count": len(affected["repeat"]),
            },
            {
                "id": "BN003",
                "type": "rework",
                "stage": "Placement Re-allocation",
                "description": "Host placement re-allocated (loop-back) before the booking is confirmed.",
                "affected_cases": affected["rework"],
                "affected_count": len(affected["rework"]),
            },
        ],
    }
    gt_path = Path(__file__).parent / "ground_truth.json"
    gt_path.write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    print(f"Wrote {xlsx_path}  ({len(all_rows)} event rows, {N_CASES} cases)")
    print(f"Wrote {csv_path}  (paste into Sheet1 of the live mock Google Sheet)")
    for name in OPS_NOTES:
        print(f"Wrote {out_dir / name}")
    print(f"Wrote {gt_path}")
    print("Injected bottlenecks:",
          {b['id']: b['affected_count'] for b in ground_truth['bottlenecks']})


if __name__ == "__main__":
    main()
