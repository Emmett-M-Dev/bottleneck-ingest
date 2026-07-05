"""Reads from the live mock Google Sheet via the Sheets API.

Returns raw dicts with the same shape as `excel_reader` output (original headers +
`_source_ref`), so the pipeline cannot tell a local file from the live Sheet.

First run opens a browser for OAuth (log in to the mock Google account + Allow);
the token is cached to credentials/token.json and all later runs are silent.
"""

from __future__ import annotations

from googleapiclient.discovery import build
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from config import CREDS_PATH, TOKEN_PATH, SHEET_ID, SHEET_TAB, SHEET_RANGE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def get_credentials(scopes: list[str] | None = None):
    """First run: opens browser for OAuth login. Later runs: loads token.json silently.

    `scopes` defaults to read-only Sheets. If a cached token is missing a requested
    scope (e.g. the Foyle path also needs Drive listing), re-consent is forced so the
    token is re-minted with the wider scope."""
    scopes = scopes or SCOPES
    creds = None
    if TOKEN_PATH.exists():
        # Load WITHOUT passing scopes, so creds.scopes reflects what the token was
        # actually granted (passing scopes would just echo the request back).
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH))
        if not set(scopes).issubset(set(creds.scopes or [])):
            creds = None  # cached token lacks a required scope -> re-consent (browser)
    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed = True
            except RefreshError:
                # Refresh token revoked/expired (Google test apps expire them after a
                # few days) -> fall through to a fresh browser consent rather than crash.
                creds = None
        if not refreshed:
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), scopes)
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
