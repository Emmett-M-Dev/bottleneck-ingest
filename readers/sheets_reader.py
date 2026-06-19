"""Reads from the live mock Google Sheet via the Sheets API.

Returns raw dicts with the same shape as `excel_reader` output (original headers +
`_source_ref`), so the pipeline cannot tell a local file from the live Sheet.

First run opens a browser for OAuth (log in to the mock Google account + Allow);
the token is cached to credentials/token.json and all later runs are silent.
"""

from __future__ import annotations

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from config import CREDS_PATH, TOKEN_PATH, SHEET_ID, SHEET_TAB, SHEET_RANGE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def get_credentials():
    """First run: opens browser for OAuth login. Later runs: loads token.json silently."""
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return creds


def read_sheet() -> list[dict]:
    """Returns list of raw dicts — same shape as excel_reader output.

    source_ref format: "sheets:{SHEET_ID}:{row_number}".
    """
    creds = get_credentials()
    service = build("sheets", "v4", credentials=creds)
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!{SHEET_RANGE}",
    ).execute()
    values = result.get("values", [])
    if not values:
        return []
    headers = values[0]
    rows = []
    for i, row in enumerate(values[1:], start=2):
        padded = row + [None] * (len(headers) - len(row))
        record = {h: (v if v != "" else None) for h, v in zip(headers, padded)}
        record["_source_ref"] = f"sheets:{SHEET_ID}:{i}"
        rows.append(record)
    return rows
