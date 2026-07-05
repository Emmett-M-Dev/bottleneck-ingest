"""Generate the streamlined Foyle Placement Tracker (real single-sheet model).

The SharePoint audit showed Foyle keeps no master tracker and no invoice/document
sheets — the real workflow is one row per sending-org cohort with sequential
pre-arrival tasks (the "Pre arrival To do" sheet), filled in with inconsistent
freetext. This builder models the streamlined, dropdown-locked REPLACEMENT we are
proposing back to them: one row per cohort, a fixed status vocabulary, and a
completion date on the milestone tasks.

Seeded bottlenecks (mirrored into ground_truth_foyle_tracker.json):
    delay        — a milestone (accommodation / placement) locked within a few
                   days of arrival, or after it
    outstanding  — a critical task still open (Not Started / In Progress) at arrival
    deposit      — a self-catering cohort whose SC deposit is unpaid

EVERYTHING is synthetic. Sending orgs, cohort ids, dates are fabricated. The
schema mirrors the real sheet's structure only, never its data.

Run:  python synthetic/generate_foyle_tracker.py
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

SEED = 11
OUT_FILE = config.FOYLE_TRACKER_FILE
GT_FILE = config.DATA_SYNTHETIC / "ground_truth_foyle_tracker.json"

STATUS_DONE = "Complete"
STATUS_NA = "N/A"
STATUS_OPEN = ["Not Started", "In Progress"]

# Fabricated sending organisations (agency-style names, not people).
SENDING_ORGS = [
    "GEB Bamberg", "IHK-Spangler", "SMMP Menden", "FOSBOS Traunstein",
    "GEB Rostock", "Mission Locale Reunion", "IBB Dresden", "Ici et Ailleurs",
    "GEB Coburg", "OSZ Berlin", "Bestwig Kolleg", "NWRC Partners",
]
PROGRAMMES = ["Internship", "Short Programme", "Homestay Only", "Job Shadowing"]
ACCOM_TYPES = ["Homestay", "Self-Catering", "Hotel"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _status_and_date(complete: bool, arrival: datetime, days_before: int) -> tuple[str, str]:
    """A milestone's (status, completion-date). Complete milestones get a real date
    `days_before` the arrival; open ones get no date."""
    if complete:
        return STATUS_DONE, _iso(arrival - timedelta(days=days_before))
    return random.choice(STATUS_OPEN), ""


def build_rows() -> tuple[list[dict], dict]:
    rows: list[dict] = []
    gt = {"delay": [], "outstanding": [], "deposit": []}

    base = datetime(2026, 2, 1)
    for i, org in enumerate(SENDING_ORGS):
        cid = f"FIE-2026-{i + 1:03d}"
        arrival = base + timedelta(days=i * 11 + random.randint(0, 4))
        departure = arrival + timedelta(days=random.choice([21, 28, 35]))
        accom = random.choice(ACCOM_TYPES)
        is_sc = accom == "Self-Catering"

        # Seed the three bottleneck patterns across roughly a third of cohorts each.
        late_milestone = i % 3 == 0          # accom/placement locked just before arrival
        critical_open = i % 4 == 1           # a critical task still open at arrival
        deposit_unpaid = is_sc and i % 2 == 0  # self-catering deposit not received

        row = {"Cohort ID": cid, "Sending Org": org,
               "Programme Type": random.choice(PROGRAMMES), "Accom Type": accom,
               "Student Count": random.randint(1, 20),
               "Arrival Date": _iso(arrival), "Departure Date": _iso(departure)}

        # Enrolment (critical, status-only)
        row["Enrolment"] = random.choice(STATUS_OPEN) if (critical_open and i % 2 == 0) else STATUS_DONE

        # Documents (critical, dated)
        docs_open = critical_open and i % 2 == 1
        row["Documents (CV/Letter)"], row["Docs Date"] = _status_and_date(
            not docs_open, arrival, random.randint(20, 40))

        # Placement Confirmed (critical, dated) — a late one is the delay signal
        plc_days = random.randint(2, 5) if late_milestone else random.randint(14, 30)
        row["Placement Confirmed"], row["Placement Date"] = _status_and_date(True, arrival, plc_days)

        # Accommodation Assigned (critical, dated) — also carries the delay signal
        accom_days = random.randint(1, 4) if late_milestone else random.randint(12, 28)
        row["Accommodation Assigned"], row["Accom Date"] = _status_and_date(True, arrival, accom_days)

        # Arrival Info (non-critical, dated)
        row["Arrival Info Received"], row["Arrival Info Date"] = _status_and_date(
            random.random() > 0.2, arrival, random.randint(7, 20))

        # SC Deposit (non-critical, dated) — N/A unless self-catering
        if is_sc:
            if deposit_unpaid:
                row["SC Deposit Received"], row["Deposit Date"] = random.choice(STATUS_OPEN), ""
            else:
                row["SC Deposit Received"], row["Deposit Date"] = STATUS_DONE, _iso(
                    arrival - timedelta(days=random.randint(10, 25)))
        else:
            row["SC Deposit Received"], row["Deposit Date"] = STATUS_NA, ""

        # Secondary status-only tasks
        for task in ("Airport Transfer", "Welcome Pack Sent", "T&Cs Signed",
                     "Local Transport", "Departure Details", "Feedback Sent"):
            row[task] = random.choice([STATUS_DONE, STATUS_DONE, random.choice(STATUS_OPEN)])

        row["Notes"] = ""
        rows.append(row)

        if late_milestone:
            gt["delay"].append(cid)
        if row["Enrolment"] in STATUS_OPEN or docs_open:
            gt["outstanding"].append(cid)
        if deposit_unpaid:
            gt["deposit"].append(cid)

    return rows, gt


def main() -> None:
    random.seed(SEED)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    rows, gt = build_rows()

    # Column order: identity, then each task with its date column right after.
    cols = ["Cohort ID", "Sending Org", "Programme Type", "Accom Type",
            "Student Count", "Arrival Date", "Departure Date"]
    for status_col, date_col, _ in config.FOYLE_TASKS:
        cols.append(status_col)
        if date_col:
            cols.append(date_col)
    cols.append("Notes")

    df = pd.DataFrame(rows, columns=cols)
    df.to_excel(OUT_FILE, index=False, engine="openpyxl")

    payload = {"generated_at": datetime.now().isoformat(), "seed": SEED,
               "cohorts": len(rows), "bottlenecks": gt}
    GT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote {df.shape[0]} cohorts x {df.shape[1]} cols to {OUT_FILE}")
    print(f"Seeded: {len(gt['delay'])} late-milestone, "
          f"{len(gt['outstanding'])} outstanding-at-arrival, "
          f"{len(gt['deposit'])} unpaid deposits")


if __name__ == "__main__":
    main()
