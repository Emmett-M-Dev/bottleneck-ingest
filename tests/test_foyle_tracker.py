"""Tests for the single-sheet Foyle Placement Tracker model: event derivation from
a cohort row and the three tracker detectors (lead-time delay, outstanding critical
task, unpaid self-catering deposit)."""

from __future__ import annotations

import pandas as pd

from detection.detect import detect_foyle_tracker
from readers.foyle_tracker_reader import _derive_tracker


def _df(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp", "actor", "status", "source_ref"])
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _by_id(results):
    return {b.id: b for b in results}


def test_derive_tracker_emits_task_and_arrival_events() -> None:
    frame = pd.DataFrame([{
        "Cohort ID": "FIE-2026-001", "Sending Org": "GEB Bamberg",
        "Programme Type": "Internship", "Accom Type": "Homestay", "Student Count": "12",
        "Arrival Date": "2026-04-07", "Departure Date": "2026-05-04",
        "Enrolment": "Complete",
        "Documents (CV/Letter)": "Complete", "Docs Date": "2026-03-10",
        "Placement Confirmed": "Complete", "Placement Date": "2026-03-20",
        "Accommodation Assigned": "Complete", "Accom Date": "2026-03-15",
        "Arrival Info Received": "Complete", "Arrival Info Date": "2026-03-25",
        "SC Deposit Received": "N/A", "Deposit Date": "",
        "Airport Transfer": "Complete", "Welcome Pack Sent": "Complete",
        "T&Cs Signed": "Complete", "Local Transport": "Complete",
        "Departure Details": "Complete", "Feedback Sent": "Not Started", "Notes": "",
    }])
    events, docs = _derive_tracker(frame)

    acts = {e["activity"] for e in events}
    assert "Arrival" in acts
    assert {"Enrolment", "Placement Confirmed", "Accommodation Assigned"} <= acts
    # every event belongs to the one cohort; the milestone carries its real date
    assert {e["case_id"] for e in events} == {"FIE-2026-001"}
    accom = next(e for e in events if e["activity"] == "Accommodation Assigned")
    assert accom["timestamp"].startswith("2026-03-15")
    assert len(docs) == 1


def test_leadtime_delay_flags_late_milestone() -> None:
    df = _df([
        # accommodation locked 3 days before arrival -> flagged
        ("C1", "Accommodation Assigned", "2026-04-04", None, "Complete", "foyle_tracker:2"),
        ("C1", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:2"),
        # accommodation locked 20 days before arrival -> fine
        ("C2", "Accommodation Assigned", "2026-03-18", None, "Complete", "foyle_tracker:3"),
        ("C2", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:3"),
    ])
    res = _by_id(detect_foyle_tracker(df))
    assert res["BN001"].affected_cases == ["C1"]
    assert res["BN001"].metric_value < 7


def test_outstanding_flags_open_critical_task() -> None:
    df = _df([
        ("C1", "Enrolment", "2026-03-01", None, "In Progress", "foyle_tracker:2"),
        ("C1", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:2"),
        # a settled cohort (Complete / N/A) is not flagged
        ("C2", "Enrolment", "2026-03-01", None, "Complete", "foyle_tracker:3"),
        ("C2", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:3"),
    ])
    res = _by_id(detect_foyle_tracker(df))
    assert res["BN002"].affected_cases == ["C1"]


def test_deposit_flags_unpaid_self_catering_only() -> None:
    df = _df([
        ("C1", "SC Deposit Received", "2026-03-01", None, "Not Started", "foyle_tracker:2"),
        ("C1", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:2"),
        # homestay cohort: deposit is N/A -> settled, not flagged
        ("C2", "SC Deposit Received", "2026-03-01", None, "N/A", "foyle_tracker:3"),
        ("C2", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:3"),
    ])
    res = _by_id(detect_foyle_tracker(df))
    assert res["BN003"].affected_cases == ["C1"]


def test_tracker_always_returns_three_bottlenecks() -> None:
    res = _by_id(detect_foyle_tracker(_df([
        ("C1", "Arrival", "2026-04-07", None, "arrived", "foyle_tracker:2")])))
    assert set(res) == {"BN001", "BN002", "BN003"}
    assert all(b.affected_count == 0 for b in res.values())
