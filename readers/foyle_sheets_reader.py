"""Live Google Drive reader for the multi-sheet Foyle model.

Reads the six Foyle sheets from a Drive folder (the foyle.mock.sme account) instead
of local xlsx, then reuses `foyle_reader._derive` so scrubbing, detection and export
are byte-for-byte the same as the local path — only the source differs.

Auto-discovery: every spreadsheet in `config.FOYLE_DRIVE_FOLDER_ID` is listed and its
title matched (case-insensitively, spaces/hyphens -> underscores) to a canonical sheet
key. Unknown titles are ignored with a warning, so dropping a new sheet in the folder
is picked up automatically.

Emails are not read from Drive yet (deferred) — the RAG corpus here is the sheet rows.
"""

from __future__ import annotations

import logging

import pandas as pd
from googleapiclient.discovery import build

import config
from readers.foyle_reader import SHEET_KEYS, _derive
from readers.sheets_reader import get_credentials

logger = logging.getLogger(__name__)

_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


def _values_to_frame(values: list[list]) -> pd.DataFrame:
    """Sheets API values (row 0 = header) -> DataFrame, same shape as the xlsx reader
    (all strings, blanks as empty string, short rows padded)."""
    if not values:
        return pd.DataFrame()
    header = values[0]
    rows = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(header) - len(row))
        rows.append({h: (v if v is not None else "") for h, v in zip(header, padded)})
    return pd.DataFrame(rows, columns=header).fillna("")


def _match_key(title: str) -> str | None:
    key = title.strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in SHEET_KEYS else None


def read_foyle_sheets() -> tuple[list[dict], list[dict]]:
    if not config.FOYLE_DRIVE_FOLDER_ID:
        raise RuntimeError(
            "config.FOYLE_DRIVE_FOLDER_ID is empty — set it to the Drive folder id "
            "that holds the six Foyle sheets (foyle.mock.sme account)."
        )
    creds = get_credentials(config.FOYLE_SCOPES)
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    query = (
        f"'{config.FOYLE_DRIVE_FOLDER_ID}' in parents "
        f"and mimeType='{_SPREADSHEET_MIME}' and trashed=false"
    )
    files = drive.files().list(q=query, fields="files(id,name)", pageSize=100).execute().get("files", [])

    frames: dict[str, pd.DataFrame] = {}
    for f in files:
        key = _match_key(f["name"])
        if not key:
            logger.warning("Ignoring Drive sheet %r — not one of %s", f["name"], SHEET_KEYS)
            continue
        result = sheets.spreadsheets().values().get(
            spreadsheetId=f["id"], range="A:Z"
        ).execute()
        frames[key] = _values_to_frame(result.get("values", []))

    missing = [k for k in SHEET_KEYS if k not in frames]
    if missing:
        logger.warning("Drive folder is missing expected sheets: %s", missing)

    return _derive(frames, email_texts=None)
