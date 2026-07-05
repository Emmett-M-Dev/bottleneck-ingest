"""Audit-agent tests: drive scanning, the zero-PII payload guarantee, the
heuristic baseline, and the LLM call against a stub client (never the network).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import config
from audit.propose import baseline_proposal, build_proposal
from audit.scan import scan_drive
from audit.schemas import (ColumnMapping, LLMFileProposal, LLMProposal,
                           MappingProposal, MessReport)


@pytest.fixture()
def tiny_drive(tmp_path: Path) -> Path:
    """One canonical-headed bookings sheet + one irrelevant sheet, seeded with
    every PII shape the scrubber must catch deterministically."""
    bookings = pd.DataFrame([
        {"Booking Ref": "B-001", "Stage": "Enquiry", "Date": "01/03/2026",
         "Handled By": "Una Toner", "Status": "done"},        # bare name spaCy misses
        {"Booking Ref": "B-001", "Stage": "booking confirmed", "Date": "04/03/2026",
         "Handled By": "Aoife Brennan", "Status": "waiting on family"},
    ])
    staff = pd.DataFrame([
        {"Name": "Una Toner", "Mobile": "+44 7700 900123",
         "Email": "una.toner@example.com", "Home": "BT48 7NN"},
    ])
    bookings.to_excel(tmp_path / "bookings.xlsx", index=False)
    staff.to_excel(tmp_path / "staff list.xlsx", index=False)
    return tmp_path


def test_scan_shape(tiny_drive: Path) -> None:
    scan = scan_drive(tiny_drive)
    assert {s["filename"] for s in scan["sheets"]} == {"bookings.xlsx", "staff list.xlsx"}
    bookings = next(s for s in scan["sheets"] if s["filename"] == "bookings.xlsx")
    assert bookings["n_rows"] == 2
    assert bookings["headers"] == ["Booking Ref", "Stage", "Date", "Handled By", "Status"]
    assert len(bookings["sample_rows"]) == 2
    # non-PII signal survives scrubbing
    assert bookings["sample_rows"][0]["Booking Ref"] == "B-001"
    assert bookings["sample_rows"][0]["Date"] == "01/03/2026"


def test_scan_payload_has_zero_pii(tiny_drive: Path) -> None:
    """Nothing raw reaches the API: names (incl. the bare-name shapes NER
    misses), emails, phones and postcodes must all be gone from the payload."""
    payload = json.dumps(scan_drive(tiny_drive), ensure_ascii=False)
    for leak in ("Una", "Toner", "Aoife", "Brennan",
                 "7700", "900123", "una.toner", "example.com", "BT48"):
        assert leak not in payload, leak


def test_scan_masks_names_stably(tiny_drive: Path) -> None:
    scan = scan_drive(tiny_drive)
    rows = {s["filename"]: s["sample_rows"] for s in scan["sheets"]}
    una_in_bookings = rows["bookings.xlsx"][0]["Handled By"]
    una_in_staff = rows["staff list.xlsx"][0]["Name"]
    assert una_in_bookings.startswith("[MASKED_")
    # same original -> same placeholder across files (cross-file signal survives)
    assert una_in_bookings == una_in_staff


def test_scan_missing_folder() -> None:
    with pytest.raises(FileNotFoundError):
        scan_drive(Path("does/not/exist"))


def test_baseline_resolves_aliases_but_not_the_fork() -> None:
    """The heuristic recognises alias headers (Booking Ref/Stage/...) but not
    renamed ones (Ref #/Step/...) — the gap the LLM exists to close."""
    scan = {"drive": "x", "sheets": [
        {"filename": "old.xlsx", "sheet": "Sheet1", "n_rows": 5,
         "headers": ["Booking Ref", "Stage", "Date", "Handled By", "Status"],
         "sample_rows": []},
        {"filename": "fork.xlsx", "sheet": "Sheet1", "n_rows": 5,
         "headers": ["Ref #", "Step", "Updated On", "Staff Member", "Payment status"],
         "sample_rows": []},
    ]}
    baseline = baseline_proposal(scan)
    by_name = {f.filename: f for f in baseline.files}
    assert by_name["old.xlsx"].inferred_role == "events"
    resolved = {c.source_header: c.canonical_field for c in by_name["old.xlsx"].columns}
    assert resolved["Booking Ref"] == "case_id"
    assert resolved["Handled By"] == "actor"
    # the fork: only "Step" is an alias -> under the events threshold
    assert by_name["fork.xlsx"].inferred_role == "ignore"


class _StubMessages:
    def __init__(self, proposal: LLMProposal) -> None:
        self._proposal = proposal
        self.last_kwargs: dict = {}

    def parse(self, **kwargs):
        self.last_kwargs = kwargs

        class _Resp:
            parsed_output = self._proposal

        return _Resp()


class _StubClient:
    def __init__(self, proposal: LLMProposal) -> None:
        self.messages = _StubMessages(proposal)


def _canned_proposal() -> LLMProposal:
    return LLMProposal(
        files=[LLMFileProposal(
            filename="bookings.xlsx", sheet="Sheet1", inferred_role="events",
            role_confidence=0.9, include=True,
            columns=[ColumnMapping(source_header="Booking Ref",
                                   canonical_field="case_id", confidence=0.95)],
            issues=[],
        )],
        mess_report=MessReport(duplicates=[], overlaps=[], gaps=[],
                               irrelevant=["staff list.xlsx"]),
    )


def test_llm_mode_uses_injected_client(tiny_drive: Path) -> None:
    client = _StubClient(_canned_proposal())
    proposal = build_proposal(tiny_drive, "foyle", client=client)

    assert proposal.mode == "llm"
    assert proposal.files[0].filename == "bookings.xlsx"
    assert proposal.files[0].n_rows == 2          # joined back from the scan
    assert proposal.baseline is not None          # eval condition rides along
    sent = client.messages.last_kwargs
    assert sent["output_format"] is LLMProposal
    assert "temperature" not in sent              # 400 on Opus 4.8
    assert "Toner" not in sent["messages"][0]["content"]


def test_offline_mode_promotes_baseline(tiny_drive: Path) -> None:
    proposal = build_proposal(tiny_drive, "foyle", offline=True)
    assert proposal.mode == "offline"
    assert proposal.model == "none"
    by_name = {f.filename: f for f in proposal.files}
    assert by_name["bookings.xlsx"].inferred_role == "events"
    # round-trips through the pydantic schema
    assert MappingProposal.model_validate(proposal.model_dump())


def test_missing_api_key_is_actionable(tiny_drive: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="--offline"):
        build_proposal(tiny_drive, "foyle")
