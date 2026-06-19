"""Reads any .xlsx in a folder into raw dicts (original headers + _source_ref).

Mapping to canonical fields happens later in the pipeline via the shared
`mapping` helper, but this reader validates up front that the sheet's columns
*can* be mapped and raises a clear error if none match — it never silently drops
data. Mixed date formats are preserved as strings (normalised downstream); blank
cells become None; surrounding whitespace is stripped.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import COLUMN_MAP
from mapping import resolve_columns


def _clean(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    return s or None


def _read_one(path: Path) -> list[dict]:
    df = pd.read_excel(path, dtype=str, engine="openpyxl")
    headers = list(df.columns)

    resolved = resolve_columns(headers)
    if not resolved:
        raise ValueError(
            f"{path.name}: no columns matched COLUMN_MAP — refusing to silently drop data. "
            f"Found headers {headers}. Expected an alias from {COLUMN_MAP}."
        )

    rows: list[dict] = []
    # Excel row 1 is the header; data rows start at spreadsheet row 2.
    for offset, record in enumerate(df.to_dict(orient="records"), start=2):
        raw = {header: _clean(record.get(header)) for header in headers}
        raw["_source_ref"] = f"{path.name}:{offset}"
        rows.append(raw)
    return rows


def read_excel_folder(folder) -> list[dict]:
    """Read every .xlsx in `folder`. Returns raw dicts with original headers."""
    folder = Path(folder)
    rows: list[dict] = []
    for path in sorted(folder.glob("*.xlsx")):
        rows.extend(_read_one(path))
    return rows
