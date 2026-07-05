"""Mapped reader: canonical events through an approved mapping, cross-file
dedup of the stale fork, role handling, and the stale-mapping guards."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from audit.schemas import ApprovedFileMapping, ApprovedMapping
from readers.mapped_reader import read_mapped


def _approved(files: list[ApprovedFileMapping]) -> ApprovedMapping:
    return ApprovedMapping(profile="test", approved_at="2026-07-05T00:00:00+00:00",
                           source_proposal_generated_at="2026-07-05T00:00:00+00:00",
                           files=files)


_CANON = {"Booking Ref": "case_id", "Stage": "activity", "Date": "timestamp",
          "Handled By": "actor", "Status": "status"}
_FORK = {"Ref #": "case_id", "Step": "activity", "Updated On": "timestamp",
         "Staff Member": "actor", "Payment status": "status"}


@pytest.fixture()
def drive(tmp_path: Path) -> Path:
    pd.DataFrame([
        {"Booking Ref": "B-1", "Stage": "Enquiry", "Date": "01/03/2026",
         "Handled By": "A", "Status": "done"},
        {"Booking Ref": "B-1", "Stage": "BOOKING CONFIRMED ", "Date": "05/03/2026",
         "Handled By": "A", "Status": "ok"},
    ]).to_excel(tmp_path / "old.xlsx", index=False)
    pd.DataFrame([
        # same event as old.xlsx row 2, renamed headers + ISO date -> dedup target
        {"Ref #": "B-1", "Step": "booking confirmed", "Updated On": "2026-03-05",
         "Staff Member": "A", "Payment status": "paid"},
        {"Ref #": "B-2", "Step": "Enquiry", "Updated On": "2026-03-06",
         "Staff Member": "B", "Payment status": ""},
    ]).to_excel(tmp_path / "fork.xlsx", index=False)
    pd.DataFrame([
        {"Family": "The Xs", "Area": "Cityside"},
    ]).to_excel(tmp_path / "hosts.xlsx", index=False)
    pd.DataFrame([{"Name": "Z", "Mobile": "123"}]).to_excel(
        tmp_path / "phones.xlsx", index=False)
    return tmp_path


def test_canonical_events_and_source_refs(drive: Path) -> None:
    approved = _approved([ApprovedFileMapping(
        filename="old.xlsx", sheet="Sheet1", role="events", include=True,
        columns=_CANON)])
    events, docs = read_mapped(drive, approved)
    assert docs == []
    assert len(events) == 2
    first = events[0]
    assert first["case_id"] == "B-1"
    assert first["activity"] == "Enquiry"
    assert first["timestamp"].startswith("2026-03-01")  # dd/mm parsed day-first
    assert first["source_ref"] == "old.xlsx:Sheet1:2"


def test_dedup_drops_the_stale_fork_row(drive: Path) -> None:
    """old.xlsx's confirmed row and fork.xlsx's are the same event (case/
    activity-canon/timestamp) in different header styles and date formats —
    only one survives, from the earlier file in approved order."""
    approved = _approved([
        ApprovedFileMapping(filename="old.xlsx", sheet="Sheet1", role="events",
                            include=True, columns=_CANON),
        ApprovedFileMapping(filename="fork.xlsx", sheet="Sheet1", role="events",
                            include=True, columns=_FORK),
    ])
    events, _ = read_mapped(drive, approved)
    confirmed = [e for e in events if e["activity"].strip().lower() == "booking confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["source_ref"].startswith("old.xlsx")
    assert {e["case_id"] for e in events} == {"B-1", "B-2"}


def test_roles_and_include_are_honoured(drive: Path) -> None:
    approved = _approved([
        ApprovedFileMapping(filename="old.xlsx", sheet="Sheet1", role="events",
                            include=False, columns=_CANON),          # skipped
        ApprovedFileMapping(filename="hosts.xlsx", sheet="Sheet1", role="reference",
                            include=True, columns={}),               # -> doc rows
        ApprovedFileMapping(filename="phones.xlsx", sheet="Sheet1", role="ignore",
                            include=False, columns={}),              # skipped
    ])
    events, docs = read_mapped(drive, approved)
    assert events == []
    assert len(docs) == 1
    assert docs[0]["text"].startswith("hosts.xlsx — ")
    assert "Cityside" in docs[0]["text"]


def test_stale_mapping_fails_loudly(drive: Path) -> None:
    missing_file = _approved([ApprovedFileMapping(
        filename="gone.xlsx", sheet="Sheet1", role="events", include=True,
        columns=_CANON)])
    with pytest.raises(FileNotFoundError, match="re-run"):
        read_mapped(drive, missing_file)

    missing_header = _approved([ApprovedFileMapping(
        filename="old.xlsx", sheet="Sheet1", role="events", include=True,
        columns={**_CANON, "Vanished Col": None})])
    with pytest.raises(ValueError, match="Vanished Col"):
        read_mapped(drive, missing_header)
