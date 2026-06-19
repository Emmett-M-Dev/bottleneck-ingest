"""Generate synthetic SME (educational-tourism / school-group bookings) data.

Produces a deliberately messy event-log spreadsheet, plain-text ops notes, and a
ground-truth answer sheet recording three injected bottlenecks.

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
WINDOW_END = datetime(2026, 4, 1)  # enquiries start within Jan–Apr

STAGES = [
    "Enquiry",
    "Quote",
    "Booking Confirmation",
    "Payment",
    "Pre-Arrival Logistics",
    "Completed",
]

# Base gap (days) leading INTO each stage from the previous one.
NORMAL_GAPS = {
    "Quote": (1, 3),
    "Booking Confirmation": (1, 2),       # BN001 inflates this for ~60% of cases
    "Payment": (1, 4),
    "Pre-Arrival Logistics": (2, 6),
    "Completed": (3, 10),
}

BN001_DELAY_GAP = (8, 14)   # Quote -> Booking Confirmation, inflated
BN001_RATE = 0.60
BN002_RATE = 0.40           # duplicate payment re-entry into pre-arrival
BN003_RATE = 0.30           # quote revision loop-back

STAFF = [
    "Sarah Jones", "Tom Baker", "Aisha Khan", "Liam O'Neill", "Megan Doherty",
    "Carlos Mendez", "Priya Patel", "James Wilson",
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
    case_id = f"BR-{idx:04d}"
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

    # Enquiry
    emit("Enquiry", t)

    # Quote
    t += timedelta(days=random.randint(*NORMAL_GAPS["Quote"]))
    emit("Quote", t)

    # BN003 — Quote Revision loop-back before confirmation
    if inject_rework:
        t += timedelta(days=random.randint(1, 3))
        emit("Quote Revision", t)

    # Booking Confirmation — BN001 inflates the gap
    gap = BN001_DELAY_GAP if inject_delay else NORMAL_GAPS["Booking Confirmation"]
    t += timedelta(days=random.randint(*gap))
    emit("Booking Confirmation", t)

    # Payment
    t += timedelta(days=random.randint(*NORMAL_GAPS["Payment"]))
    emit("Payment", t)

    # BN002 — payment data re-entered into the pre-arrival sheet
    if inject_repeat:
        t += timedelta(days=random.randint(0, 1))
        emit("Payment Re-entry", t)

    # Pre-Arrival Logistics
    t += timedelta(days=random.randint(*NORMAL_GAPS["Pre-Arrival Logistics"]))
    emit("Pre-Arrival Logistics", t)

    # Completed
    t += timedelta(days=random.randint(*NORMAL_GAPS["Completed"]))
    emit("Completed", t)

    flags = {"delay": inject_delay, "repeat": inject_repeat, "rework": inject_rework}
    return rows, flags


OPS_NOTES = {
    "ops_notes_jan.txt": (
        "January was busy with school enquiries. Sarah Jones at Riverside Academy "
        "chased us twice about a booking confirmation that took nearly two weeks to "
        "come back. You can reach her on sarah.jones@riverside-academy.org or "
        "07700 900123.\n\n"
        "The team keeps re-typing payment details from the finance sheet into the "
        "pre-arrival logistics sheet. It is slow and we have had a couple of "
        "mismatches. We should stop doing the same data entry twice."
    ),
    "ops_notes_feb.txt": (
        "February pain points: quotes are going back and forth too much. Tom Baker "
        "revised the Greenfield College quote three times before it was confirmed. "
        "Customers in BT48 7PT and beyond are noticing the delays.\n\n"
        "Booking confirmations are still the slow step. Several groups waited 8 to 12 "
        "days. Parents start emailing to ask whether the trip is actually booked."
    ),
    "ops_notes_mar.txt": (
        "March review. The confirmation backlog has not improved. Aisha Khan flagged "
        "that the same booking can sit for over a week before anyone signs it off.\n\n"
        "Payment re-entry into the pre-arrival sheet came up again at the standup. "
        "Contact the office on 028 9012 3456 or ops@foyle-mock-sme.example if you spot "
        "a duplicate entry. We need a single source of truth."
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
                "stage": "Booking Confirmation",
                "description": "Booking Confirmation takes 8-14 days instead of 1-2.",
                "affected_cases": affected["delay"],
                "affected_count": len(affected["delay"]),
            },
            {
                "id": "BN002",
                "type": "repetition",
                "stage": "Payment Re-entry",
                "description": "Payment data re-entered into the Pre-Arrival sheet (duplicate activity after Payment).",
                "affected_cases": affected["repeat"],
                "affected_count": len(affected["repeat"]),
            },
            {
                "id": "BN003",
                "type": "rework",
                "stage": "Quote Revision",
                "description": "Quote revised (loop-back) before the booking is confirmed.",
                "affected_cases": affected["rework"],
                "affected_count": len(affected["rework"]),
            },
        ],
    }
    gt_path = Path(__file__).parent / "ground_truth.json"
    gt_path.write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    print(f"Wrote {xlsx_path}  ({len(all_rows)} event rows, {N_CASES} cases)")
    for name in OPS_NOTES:
        print(f"Wrote {out_dir / name}")
    print(f"Wrote {gt_path}")
    print("Injected bottlenecks:",
          {b['id']: b['affected_count'] for b in ground_truth['bottlenecks']})


if __name__ == "__main__":
    main()
