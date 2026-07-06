"""Collect the remediable status values from a profile's ingested drive.

Reads the human-approved mapping (so it only ever touches files the human
confirmed as event data) and returns, per events file, the distinct freetext
values in its status column and how often each occurs. That distribution is
what the proposer maps to the controlled vocabulary.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

from audit.schemas import ApprovedMapping


def _status_header(columns: dict[str, str | None]) -> str | None:
    for header, field in columns.items():
        if field == "status":
            return header
    return None


def scan_statuses(drive: Path | str, approved: ApprovedMapping) -> list[dict]:
    """Per included events file: {filename, sheet, status_column, values:
    Counter(raw -> count)}. Skips files with no mapped status column."""
    drive = Path(drive)
    out: list[dict] = []
    for f in approved.files:
        if not f.include or f.role != "events":
            continue
        status_col = _status_header(f.columns)
        if not status_col:
            continue
        path = drive / f.filename
        if not path.exists():
            continue
        df = pd.read_excel(path, sheet_name=f.sheet, dtype=str)
        if status_col not in df.columns:
            continue
        counts = Counter(
            ("" if v is None or pd.isna(v) else str(v))
            for v in df[status_col]
        )
        out.append({"filename": f.filename, "sheet": f.sheet,
                    "status_column": status_col, "values": counts})
    return out
