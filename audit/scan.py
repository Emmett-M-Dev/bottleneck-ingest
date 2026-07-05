"""Walk a messy drive folder and build the (scrubbed) payload the LLM sees.

Zero raw PII leaves this module. Two layers, because the sample cells are
context-free (spaCy NER silently misses bare names — the same weakness that
made `scrub_actor` mask its field wholesale):

1. Shape mask — any cell that LOOKS like a name (2-4 capitalised alphabetic
   tokens) is replaced with a stable ``[MASKED_n]`` placeholder outright. This
   is deterministic, so it is testable; it deliberately over-masks (a
   Title-Case stage label is masked too). The mapping task survives: headers,
   dates, IDs, freetext statuses and mixed-case labels all pass through, and a
   masked column is itself a signal ("holds person names -> actor").
2. `scrub.anonymise.scrub_text` on everything else — regex email/phone/postcode
   (deterministic) plus best-effort NER for text with context.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

import config
from scrub.anonymise import EMAIL_RE, PHONE_RE, POSTCODE_RE, scrub_text

# A "nameish" token: capitalised alphabetic word (allows O'Brien, Smith-Jones,
# ALLCAPS); lowercase particles (van/de/the/...) may appear between tokens.
_NAMEISH_TOKEN = re.compile(r"^[A-Z][a-zA-Z'’\-]*$")
_PARTICLES = {"the", "van", "de", "von", "da", "del", "der", "di", "mac", "mc", "o"}

# A single whitespace-free token containing a digit: an ID, date, or code
# ("B-001", "01/03/2026"). NER mangles these (spaCy tags short IDs as ORG),
# and they carry the strongest mapping signal — keep them verbatim. Safe only
# AFTER the PII regexes have been checked (a spaceless phone number is also
# digit-bearing).
_CODE_LIKE = re.compile(r"^[\w#/\-.:]*\d[\w#/\-.:]*$")


def _nameish(value: str) -> bool:
    tokens = value.split()
    if not 2 <= len(tokens) <= 4:
        return False
    return all(_NAMEISH_TOKEN.match(t) or t.lower() in _PARTICLES for t in tokens)


class _Masker:
    """Stable [MASKED_n] placeholders: identical originals -> same token."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def __call__(self, value: str) -> str:
        if value not in self._seen:
            self._seen[value] = f"[MASKED_{len(self._seen) + 1}]"
        return self._seen[value]


def _clean_cell(value, masker: _Masker) -> str:
    s = "" if value is None or pd.isna(value) else str(value).strip()
    s = s[: config.AUDIT_CELL_MAX_CHARS]
    if not s:
        return s
    if _nameish(s):
        return masker(s)
    if EMAIL_RE.search(s) or PHONE_RE.search(s) or POSTCODE_RE.search(s):
        return scrub_text(s)[0]
    if _CODE_LIKE.match(s):
        return s
    return scrub_text(s)[0]


def scan_drive(folder: Path | str) -> dict:
    """Headers + a few scrubbed sample rows per sheet, for every xlsx in the
    folder. Returns the plain-dict payload `audit.infer` sends to the API."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Drive folder not found: {folder}")

    masker = _Masker()
    sheets: list[dict] = []
    for path in sorted(folder.glob("*.xlsx")):
        for sheet_name, df in pd.read_excel(path, sheet_name=None, dtype=str).items():
            if len(sheets) >= config.AUDIT_MAX_SHEETS:
                break
            sample = df.head(config.AUDIT_SAMPLE_ROWS)
            sheets.append({
                "filename": path.name,
                "sheet": str(sheet_name),
                "n_rows": int(df.shape[0]),
                "headers": [str(h) for h in df.columns],
                "sample_rows": [
                    {str(h): _clean_cell(v, masker) for h, v in row.items()}
                    for row in sample.to_dict(orient="records")
                ],
            })

    if not sheets:
        raise ValueError(f"No .xlsx files found in {folder}")
    return {"drive": folder.name, "sheets": sheets}
