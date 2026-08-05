"""Reader tests: excel mapping + cleaning, text paragraph split."""

from __future__ import annotations

import pandas as pd
import pytest

from readers import excel_reader, text_reader


# ── excel_reader ─────────────────────────────────────────────────────────────
def test_excel_reads_maps_and_cleans(tmp_path) -> None:
    path = tmp_path / "tracker.xlsx"
    pd.DataFrame(
        [
            {"Booking Ref": "BR-0001", "Stage": " Enquiry ", "Date": "2026-01-05",
             "Handled By": "Sarah Jones", "Status": "done"},
            {"Booking Ref": "BR-0001", "Stage": "Quote", "Date": "06/01/2026",
             "Handled By": None, "Status": None},
        ]
    ).to_excel(path, index=False, engine="openpyxl")

    rows = excel_reader.read_excel_folder(tmp_path)
    assert len(rows) == 2
    # whitespace stripped
    assert rows[0]["Stage"] == "Enquiry"
    # blanks -> None
    assert rows[1]["Handled By"] is None
    assert rows[1]["Status"] is None
    # source_ref: file:row, data starts at spreadsheet row 2
    assert rows[0]["_source_ref"] == "tracker.xlsx:2"
    assert rows[1]["_source_ref"] == "tracker.xlsx:3"


def test_excel_raises_when_no_columns_match(tmp_path) -> None:
    path = tmp_path / "unknown.xlsx"
    pd.DataFrame([{"Foo": 1, "Bar": 2}]).to_excel(path, index=False, engine="openpyxl")
    with pytest.raises(ValueError, match="no columns matched COLUMN_MAP"):
        excel_reader.read_excel_folder(tmp_path)


# ── text_reader ──────────────────────────────────────────────────────────────
def test_text_splits_on_blank_line(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("Para one.\n\nPara two.\n\n\nPara three.", encoding="utf-8")
    rows = text_reader.read_text_folder(tmp_path)
    assert [r["text"] for r in rows] == ["Para one.", "Para two.", "Para three."]
    assert all(r["source_ref"] == "notes.txt" for r in rows)
