"""Column-mapping helper — the single place source headers become canonical fields.

Both `readers/excel_reader.py` (to validate + fail fast) and `pipeline/normalise.py`
(to extract canonical fields) use this, so the alias logic lives in exactly one place.
Update `config.COLUMN_MAP` to support a new data source; nothing here changes.
"""

from __future__ import annotations

from config import COLUMN_MAP


def _norm(h: str | None) -> str | None:
    return h.strip().lower() if isinstance(h, str) else h


def resolve_columns(headers: list, column_map: dict = COLUMN_MAP) -> dict:
    """Map canonical field name -> the actual source header present in `headers`.

    Case-insensitive, whitespace-tolerant. Canonical fields with no matching alias
    are simply absent from the result.
    """
    norm_to_orig = {}
    for h in headers:
        n = _norm(h)
        if n is not None and n not in norm_to_orig:
            norm_to_orig[n] = h
    resolved: dict = {}
    for canonical, aliases in column_map.items():
        for alias in aliases:
            if _norm(alias) in norm_to_orig:
                resolved[canonical] = norm_to_orig[_norm(alias)]
                break
    return resolved


def map_row(raw: dict, column_map: dict = COLUMN_MAP) -> dict:
    """Extract canonical fields from one raw reader dict (ignores `_`-prefixed keys)."""
    headers = [k for k in raw.keys() if not str(k).startswith("_")]
    resolved = resolve_columns(headers, column_map)
    return {canonical: raw.get(src) for canonical, src in resolved.items()}
