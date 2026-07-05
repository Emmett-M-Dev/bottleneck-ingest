"""Reader for the Foyle Placement Tracker (real single-sheet model).

One row per sending-org cohort; columns are the sequential pre-arrival tasks
defined in `config.FOYLE_TASKS`. From each row we derive:

  - event_rows : one canonical event per (cohort, task). case_id is the Cohort ID
                 (an org-level code, not a person, so no PII). Milestone tasks carry
                 their real completion date; status-only tasks get an arrival-relative
                 timestamp purely to order the workflow map. A terminal Arrival event
                 closes each case.
  - doc_rows   : one free-text snippet per cohort (org / programme / notes) for RAG.

Unlike the six-sheet model there is no cross-sheet name reconciliation — a cohort
is identified by its stable Cohort ID, so `_canon_name` and the wobble-matching
logic drop out entirely.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

import config


def _iso(value) -> str | None:
    # The tracker uses ISO YYYY-MM-DD dates, so no dayfirst — pandas parses ISO
    # natively. (dayfirst=True would swap month/day on year-first strings.)
    if value in (None, ""):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else ts.isoformat()


def _offset_ts(arrival: pd.Timestamp | None, index: int, total: int) -> str | None:
    """Arrival-relative timestamp for a status-only task, so it lands in workflow
    order (earlier tasks sit further before arrival). Ordering only — never used
    for delay measurement."""
    if arrival is None:
        return None
    days = (total - index) * 3 + 3
    return (arrival - timedelta(days=days)).isoformat()


def read_foyle_tracker() -> tuple[list[dict], list[dict]]:
    """Local-disk reader: load the tracker xlsx and derive the event log + snippets.
    The Drive reader builds the frame differently and calls `_derive_tracker`."""
    # keep_default_na=False so "N/A" (a valid status) is not silently read as NaN.
    df = pd.read_excel(
        config.FOYLE_TRACKER_FILE, dtype=str, engine="openpyxl", keep_default_na=False
    ).fillna("")
    return _derive_tracker(df)


def _values_to_frame(values: list[list]) -> pd.DataFrame:
    """Sheets API values (row 0 = header) -> DataFrame (all strings, blanks as "",
    short rows padded). Sheets return literal strings, so "N/A" survives without the
    keep_default_na guard the xlsx path needs."""
    if not values:
        return pd.DataFrame()
    header = values[0]
    rows = []
    for row in values[1:]:
        padded = list(row) + [""] * (len(header) - len(row))
        rows.append({h: (v if v is not None else "") for h, v in zip(header, padded)})
    return pd.DataFrame(rows, columns=header).fillna("")


def read_foyle_tracker_sheets() -> tuple[list[dict], list[dict]]:
    """Live reader: find the tracker sheet in the Drive folder by title, read it via
    the Sheets API, and derive the event log — the same `_derive_tracker` the local
    xlsx path uses, so detection and export are byte-for-byte identical."""
    from googleapiclient.discovery import build  # lazy: only this path needs google libs
    from readers.sheets_reader import get_credentials

    if not config.FOYLE_TRACKER_DRIVE_FOLDER_ID:
        raise RuntimeError(
            "config.FOYLE_TRACKER_DRIVE_FOLDER_ID is empty — set it to the Drive folder "
            "id holding the tracker sheet."
        )
    creds = get_credentials(config.FOYLE_SCOPES)
    drive = build("drive", "v3", credentials=creds)
    sheets = build("sheets", "v4", credentials=creds)

    title = config.FOYLE_TRACKER_SHEET_TITLE
    query = (
        f"'{config.FOYLE_TRACKER_DRIVE_FOLDER_ID}' in parents and trashed=false "
        f"and mimeType='application/vnd.google-apps.spreadsheet' "
        f"and name='{title}'"
    )
    files = drive.files().list(q=query, fields="files(id,name)", pageSize=10).execute().get("files", [])
    if not files:
        raise RuntimeError(
            f"No Google Sheet titled {title!r} found in Drive folder "
            f"{config.FOYLE_TRACKER_DRIVE_FOLDER_ID}."
        )
    sheet_id = files[0]["id"]
    result = sheets.spreadsheets().values().get(spreadsheetId=sheet_id, range="A:Z").execute()
    frame = _values_to_frame(result.get("values", []))
    return _derive_tracker(frame)


def _derive_tracker(frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    """Derive events + RAG snippets from a tracker frame, independent of whether it
    came from local xlsx or the live Drive sheet."""
    event_rows: list[dict] = []
    doc_rows: list[dict] = []

    tasks = config.FOYLE_TASKS
    total = len(tasks)

    for i, r in frame.iterrows():
        cid = str(r.get("Cohort ID", "") or "").strip()
        if not cid:
            continue
        ref = f"foyle_tracker:{i + 2}"
        arr_iso = _iso(r.get("Arrival Date"))
        arr_dt = pd.to_datetime(arr_iso) if arr_iso else None

        for idx, (status_col, date_col, _crit) in enumerate(tasks):
            status = str(r.get(status_col, "") or "").strip()
            ts = _iso(r.get(date_col)) if date_col else None
            if not ts:
                ts = _offset_ts(arr_dt, idx, total)
            if ts:
                event_rows.append({
                    "case_id": cid, "activity": status_col, "timestamp": ts,
                    "actor": None, "status": status or None, "source_ref": ref,
                })

        if arr_iso:
            event_rows.append({
                "case_id": cid, "activity": "Arrival", "timestamp": arr_iso,
                "actor": None, "status": "arrived", "source_ref": ref,
            })

        notes = str(r.get("Notes", "") or "").strip()
        doc_rows.append({"source_ref": ref, "text":
            f"Cohort {cid} — {r.get('Sending Org')}, {r.get('Programme Type')}, "
            f"{r.get('Accom Type')} for {r.get('Student Count')} students, "
            f"arriving {arr_iso}. {notes}".strip()})

    return event_rows, doc_rows
