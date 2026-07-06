"""Remediation executor: status scan, value-map proposal (offline rules + a
stubbed LLM), and apply-to-cleaned-copy with an untouched original."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from audit.schemas import ApprovedFileMapping, ApprovedMapping
from remediate.apply import apply_plan
from remediate.propose import _rule_map, build_plan
from remediate.scan import scan_statuses


_COLS = {"Booking Ref": "case_id", "Stage": "activity", "Date": "timestamp",
         "Handled By": "actor", "Status": "status"}


def _approved(files) -> ApprovedMapping:
    return ApprovedMapping(profile="foyle", approved_at="2026-07-05T00:00:00+00:00",
                           source_proposal_generated_at="2026-07-05T00:00:00+00:00",
                           files=files)


@pytest.fixture()
def drive(tmp_path: Path) -> Path:
    pd.DataFrame([
        {"Booking Ref": "B-1", "Stage": "Enquiry", "Date": "01/03/2026",
         "Handled By": "A", "Status": "done "},
        {"Booking Ref": "B-1", "Stage": "Confirmed", "Date": "05/03/2026",
         "Handled By": "A", "Status": "DONE"},
        {"Booking Ref": "B-2", "Stage": "Enquiry", "Date": "06/03/2026",
         "Handled By": "B", "Status": "waiting on family"},
        {"Booking Ref": "B-2", "Stage": "Confirmed", "Date": "09/03/2026",
         "Handled By": "B", "Status": "Completed ✔"},
    ]).to_excel(tmp_path / "bookings.xlsx", index=False)
    # a reference file with no status mapping — must be ignored by the scan
    pd.DataFrame([{"Family": "The Xs"}]).to_excel(tmp_path / "hosts.xlsx", index=False)
    return tmp_path


@pytest.mark.parametrize("raw,expected", [
    ("done ", "Complete"), ("DONE", "Complete"), ("ok", "Complete"),
    ("Completed ✔", "Complete"), ("complete", "Complete"),
    ("waiting on family", "Open"), ("chased 2x", "Open"), ("", "Open"),
    ("N/A", "N/A"), ("n/a", "N/A"),
])
def test_rule_map(raw, expected) -> None:
    assert _rule_map(raw)[0] == expected


def test_scan_only_events_files_with_status(drive: Path) -> None:
    approved = _approved([
        ApprovedFileMapping(filename="bookings.xlsx", sheet="Sheet1", role="events",
                            include=True, columns=_COLS),
        ApprovedFileMapping(filename="hosts.xlsx", sheet="Sheet1", role="reference",
                            include=True, columns={}),
    ])
    scan = scan_statuses(drive, approved)
    assert len(scan) == 1
    assert scan[0]["status_column"] == "Status"
    assert scan[0]["values"]["done "] == 1
    assert scan[0]["values"]["DONE"] == 1


def test_offline_plan_collapses_vocabulary(drive: Path, monkeypatch) -> None:
    import config
    monkeypatch.setitem(config.MESSY_PROFILES["foyle"], "dir", drive)
    approved = _approved([ApprovedFileMapping(
        filename="bookings.xlsx", sheet="Sheet1", role="events", include=True, columns=_COLS)])

    plan = build_plan("foyle", offline=True, mapping_path=_write_mapping(drive, approved))
    assert plan.mode == "offline"
    canon = {v.canonical for f in plan.files for v in f.value_map}
    assert canon <= {"Complete", "Open", "N/A"}
    # 3 done-ish cells -> Complete, 1 freetext -> Open
    complete = sum(v.count for f in plan.files for v in f.value_map if v.canonical == "Complete")
    assert complete == 3


def test_apply_writes_cleaned_copy_original_untouched(drive: Path, monkeypatch) -> None:
    import config
    monkeypatch.setitem(config.MESSY_PROFILES["foyle"], "dir", drive)
    approved = _approved([ApprovedFileMapping(
        filename="bookings.xlsx", sheet="Sheet1", role="events", include=True, columns=_COLS)])
    plan = build_plan("foyle", offline=True, mapping_path=_write_mapping(drive, approved))

    result = apply_plan(plan)
    assert result.cells_changed == 4

    cleaned = pd.read_excel(Path(result.output_dir) / "bookings.xlsx", dtype=str)
    assert set(cleaned["Status"]) == {"Complete", "Open"}
    assert list(cleaned.columns) == ["Booking Ref", "Stage", "Date", "Handled By", "Status"]

    original = pd.read_excel(drive / "bookings.xlsx", dtype=str)
    assert set(original["Status"]) == {"done ", "DONE", "waiting on family", "Completed ✔"}


def test_stubbed_llm_plan(drive: Path, monkeypatch) -> None:
    import config
    monkeypatch.setitem(config.MESSY_PROFILES["foyle"], "dir", drive)
    approved = _approved([ApprovedFileMapping(
        filename="bookings.xlsx", sheet="Sheet1", role="events", include=True, columns=_COLS)])

    class _Parsed:
        def __init__(self, items): self.parsed_output = type("O", (), {"items": items})()

    class _Msgs:
        def parse(self, **kw):
            # map everything to Complete regardless — proves the LLM path wires through
            import json
            vals = [d["value"] for d in json.loads(kw["messages"][0]["content"])]
            Item = type("I", (), {})
            items = [type("I", (), {"original": v, "canonical": "Complete", "confidence": 0.8})()
                     for v in vals]
            return _Parsed(items)

    class _Client:
        messages = _Msgs()

    plan = build_plan("foyle", offline=False, client=_Client(),
                      mapping_path=_write_mapping(drive, approved))
    assert plan.mode == "llm"
    assert all(v.canonical == "Complete" for f in plan.files for v in f.value_map)


def _write_mapping(drive: Path, approved: ApprovedMapping) -> Path:
    import json
    p = drive / "approved.json"
    p.write_text(json.dumps(approved.model_dump()), encoding="utf-8")
    return p
