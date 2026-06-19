# Phase 1 — Ingestion + Scrub + Live Mock Environment
## Handoff brief · Claude Code agent

---

## How to read this
- **[AGENT]** — build tasks. Work these in order.
- **[YOU]** — your actions only. Do not action these as build tasks.

---

## What Phase 1 produces
By the end of this phase you have two things running in parallel:

1. **A local file pipeline** — reads `.xlsx` and `.txt` from a folder, scrubs, normalises, embeds into ChromaDB.
2. **A live Google Sheets reader** — reads from a real Google Sheet (in your own Google account) via the Sheets API, feeds the same pipeline.

Both paths produce identical output. The detection and RAG layers downstream see no difference between them. The mock Google environment makes the dissertation demo look like a real deployed system — because it is one, just on your own accounts with synthetic data.

---

# [YOU] Step 0 — Set up the mock Google environment
Do this before any build work starts. Takes about 30 minutes.

## Create the Google account
- Create a dedicated Google account for the project: something like `foyle.mock.sme@gmail.com`.
- This keeps project data separate from personal accounts.
- Share access with yourself on your main account so you can manage it easily.

## Populate it with synthetic data
After the agent generates the synthetic data (Task 3 below), you manually upload it here:
- Upload the generated `bookings_tracker.xlsx` to **Google Drive** in this account.
- Open it in Google Sheets (File → Open with Google Sheets) — it becomes a live Sheet.
- Note down the **Spreadsheet ID** from the URL: `docs.google.com/spreadsheets/d/THIS_PART_HERE/edit`
- Create a folder called `ops-notes/` in Drive and upload the generated `.txt` files there.

## Set up a Google Cloud project (this is what lets code talk to Google)
This sounds technical but is a ~15 minute web form:

1. Go to `console.cloud.google.com`
2. Create a new project — call it `foyle-mock-pipeline`
3. Enable two APIs (search for them in the API Library):
   - **Google Sheets API**
   - **Google Drive API**
4. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
   - Application type: **Desktop app**
   - Download the credentials file — save it as `credentials/google_oauth.json` in the project repo (this file is gitignored — never commit it)
5. First time you run the Sheets reader it will open a browser window asking you to log in to the mock Google account and grant permission. After that it saves a token locally and never asks again.

**What you will have after this step:**
- A live Google Sheet with synthetic booking data your code can read
- A `google_oauth.json` credentials file that lets your code authenticate
- A noted Spreadsheet ID to paste into `config.py`

---

# [AGENT] First protocol — environment and dependencies
Get everything installed and verified before writing any pipeline code.

## Project structure
```
bottleneck-ingest/
  ingest.py                    # CLI entry point
  config.py                    # all paths, model names, sheet IDs, column mappings
  models.py                    # Event, NormalisedRecord dataclasses
  synthetic/
    generate.py                # generates synthetic SME data + ground_truth.json
    ground_truth.json          # injected bottleneck labels (written by generate.py)
  readers/
    excel_reader.py            # reads local .xlsx files
    text_reader.py             # reads local .txt files
    sheets_reader.py           # reads from Google Sheets API
  scrub/
    anonymise.py               # spaCy NER + regex PII scrubbing
  pipeline/
    normalise.py               # raw dict -> NormalisedRecord + Event rows
    chunk.py                   # text chunking for embedding
    embed.py                   # local embeddings -> ChromaDB
  credentials/                 # gitignored — never committed
    google_oauth.json          # OAuth credentials from Google Cloud Console [YOU adds this]
    token.json                 # auto-generated on first auth run
  data/
    synthetic/                 # generated local files (xlsx, txt)
    real/                      # gitignored — Foyle real data lands here later
  outputs/                     # gitignored
    event_log.parquet
    records.jsonl
    chroma/                    # persisted vector store
  tests/
    test_models.py
    test_readers.py
    test_scrub.py
    test_pipeline.py
  requirements.txt
  .gitignore
  README.md
```

## requirements.txt
```
pandas
openpyxl
chromadb
sentence-transformers
spacy
pyarrow
google-api-python-client
google-auth-httplib2
google-auth-oauthlib
pytest
```

## Post-install command
```bash
python -m spacy download en_core_web_sm
```

## Smoke check (run before any other task)
```bash
python -c "
import pandas, openpyxl, chromadb, sentence_transformers, spacy
from googleapiclient.discovery import build
print('all imports ok')
"
```

## .gitignore must include
```
credentials/
data/real/
outputs/
*.pyc
__pycache__/
.env
```

---

# [AGENT] Task 1 — config.py
Central config. Everything that might change lives here. No magic strings anywhere else.

```python
from pathlib import Path

ROOT           = Path(__file__).parent
DATA_SYNTHETIC = ROOT / "data" / "synthetic"
DATA_REAL      = ROOT / "data" / "real"
OUTPUTS        = ROOT / "outputs"
CHROMA_PATH    = OUTPUTS / "chroma"
CREDS_PATH     = ROOT / "credentials" / "google_oauth.json"
TOKEN_PATH     = ROOT / "credentials" / "token.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Google Sheets — YOU fills this in after mock environment is set up
SHEET_ID    = "PASTE_SPREADSHEET_ID_HERE"
SHEET_TAB   = "Sheet1"
SHEET_RANGE = "A:Z"

# Column mapping — maps source column names -> canonical field names
# Update this when real Foyle data arrives without touching any other code
COLUMN_MAP = {
    "case_id":   ["Booking Ref", "Case ID", "Reference", "ID"],
    "activity":  ["Stage", "Activity", "Step", "Status Label"],
    "timestamp": ["Date", "Updated", "Completed Date", "Timestamp"],
    "actor":     ["Handled By", "Assigned To", "Staff", "Owner"],
    "status":    ["Status", "State", "Outcome"],
}

CHUNK_SIZE    = 400
CHUNK_OVERLAP = 80
SPACY_MODEL   = "en_core_web_sm"
SCRUB_ENTITIES = {"PERSON", "ORG", "GPE", "LOC", "FAC", "NORP"}
```

---

# [AGENT] Task 2 — models.py
The data contract. Everything downstream consumes these shapes.

```python
from dataclasses import dataclass

@dataclass
class Event:
    case_id:    str
    activity:   str
    timestamp:  str           # ISO 8601
    actor:      str | None    # scrubbed to placeholder
    status:     str | None
    source_ref: str           # filename or "sheets:{sheet_id}:{row}"

@dataclass
class NormalisedRecord:
    record_id:         str
    source_type:       str    # "excel" | "text" | "sheets"
    source_ref:        str
    ingested_at:       str    # ISO 8601
    text:              str    # scrubbed, used for embedding
    structured:        dict   # scrubbed key-value fields
    events:            list[Event]
    scrubbed_entities: list[dict]  # [{type, placeholder}] — originals never stored
```

Tests: round-trip to/from dict for both dataclasses.

---

# [AGENT] Task 3 — synthetic/generate.py
Generates realistic fake SME data and the ground-truth answer sheet.

Simulate a small educational-tourism SME (school group bookings) with:
- **~150 cases** over a **4-month window** (Jan–Apr)
- **Six stages per case:** `Enquiry → Quote → Booking Confirmation → Payment → Pre-Arrival Logistics → Completed`
- Normal stage durations sampled from realistic distributions

Inject exactly three known bottlenecks and record them in `ground_truth.json`:

| ID | Type | What to inject | How |
|---|---|---|---|
| BN001 | Delay | Booking Confirmation takes 8–14 days instead of 1–2 | Inflate duration for ~60% of cases |
| BN002 | Repetition | Payment data re-entered into Pre-Arrival sheet | Add duplicate activity after Payment for ~40% of cases |
| BN003 | Rework | Quote revised before confirmed | Add Quote Revision loop-back for ~30% of cases |

Output files:
- `data/synthetic/bookings_tracker.xlsx` — messy tracker with intentional issues (mixed date formats, some blank cells, inconsistent capitalisation)
- `data/synthetic/ops_notes_jan.txt`, `ops_notes_feb.txt`, `ops_notes_mar.txt` — short plain-text notes mentioning recurring pain points

**Critical rule:** `generate.py` must never be imported by any pipeline or detection code. The detector sees only `event_log.parquet` and must rediscover the bottlenecks independently.

---

# [AGENT] Task 4 — readers/excel_reader.py and readers/text_reader.py

## excel_reader.py
- Reads any `.xlsx` in a given folder using pandas + openpyxl
- Maps column names to canonical fields using `config.COLUMN_MAP`
- Raises a clear error if no alias matches — never silently drops data
- Handles mixed date formats, blank cells (None), extra whitespace

## text_reader.py
- Reads any `.txt` in a given folder
- Splits on double newline into paragraphs
- Returns `[{"text": str, "source_ref": filename}]`
- No structure extraction — feeds RAG as-is after scrubbing

---

# [AGENT] Task 5 — readers/sheets_reader.py
Reads from the live mock Google Sheet via the Sheets API.

```python
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from config import CREDS_PATH, TOKEN_PATH, SHEET_ID, SHEET_TAB, SHEET_RANGE

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def get_credentials():
    """
    First run: opens browser for OAuth login.
    Subsequent runs: loads saved token.json silently.
    """
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
    """
    Returns list of raw dicts — same shape as excel_reader output.
    source_ref format: "sheets:{SHEET_ID}:{row_number}"
    """
    creds   = get_credentials()
    service = build("sheets", "v4", credentials=creds)
    result  = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!{SHEET_RANGE}"
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
```

Note for YOU: first run opens a browser. Log in to the mock Google account and click Allow. Token saved to `credentials/token.json` — all future runs are silent.

Tests: mock the API response with a fixture dict and assert correct row parsing and source_ref format.

---

# [AGENT] Task 6 — scrub/anonymise.py
Runs before anything is stored or embedded. No raw PII ever reaches ChromaDB, records.jsonl, or event_log.parquet.

Implements:
- spaCy NER replacing PERSON, ORG, GPE, LOC, FAC, NORP with stable placeholders (`[PERSON_1]`, `[ORG_1]` etc.)
- Regex scrubbing for email addresses, phone numbers, postcodes
- Dates and numeric values pass through unchanged
- Returns scrubbed text + list of `{type, placeholder}` replacements — originals never stored

Two functions: `scrub_text(text) -> (scrubbed_str, replacements)` and `scrub_dict(record) -> (scrubbed_dict, replacements)`

Tests must assert: seeded name, email, phone, postcode do not appear anywhere in output. Dates and numbers pass through unchanged.

---

# [AGENT] Task 7 — pipeline/normalise.py
Converts raw reader output into NormalisedRecord + Event rows.

- Accepts raw dicts from any reader (excel, text, sheets — identical shape by this point)
- Applies COLUMN_MAP to extract canonical fields
- Calls scrub_dict on all string fields before storing anything
- Builds one NormalisedRecord per row
- Extracts one Event per row where case_id + activity + timestamp are all present
- Logs (does not crash) on rows where mapping is incomplete

---

# [AGENT] Task 8 — pipeline/chunk.py and pipeline/embed.py

## chunk.py
- Splits NormalisedRecord.text into overlapping chunks (size and overlap from config)
- Preserves source_ref and record_id in chunk metadata
- Returns `list[{"text": str, "metadata": dict}]`

## embed.py
- Loads all-MiniLM-L6-v2 via sentence-transformers (local, no API)
- Initialises persistent ChromaDB client at config.CHROMA_PATH
- Upserts chunks into collection `"sme_ops"`
- Metadata per chunk: record_id, source_ref, source_type, ingested_at
- Smoke test: query `"delays in booking confirmation"` and assert at least one result returns

---

# [AGENT] Task 9 — ingest.py CLI and README.md

## ingest.py — three modes
```
python ingest.py --source local     # reads from data/synthetic/ (xlsx + txt)
python ingest.py --source sheets    # reads from live Google Sheet
python ingest.py --source all       # runs both, deduplicates by source_ref
```

Each mode: read → scrub → normalise → write parquet + jsonl → embed into ChromaDB.

Print a summary on completion:
```
[local]  Read 150 rows from 1 xlsx, 3 txt files
[sheets] Read 150 rows from Google Sheet (mock env)
Scrubbed: 47 entities replaced
Records:  150 written to outputs/records.jsonl
Events:   612 rows written to outputs/event_log.parquet
Embedded: 284 chunks into ChromaDB collection 'sme_ops'
```

## README.md must cover
- One-time setup (install deps, spaCy download, Google OAuth first-run steps)
- How to run each mode
- Where outputs land
- How to update column mapping for a new data source (point to config.py only)
- Note that credentials/ and data/real/ are gitignored and never committed

---

# [AGENT] Definition of done

- [ ] `python ingest.py --source local` runs on synthetic data → parquet + jsonl + ChromaDB
- [ ] `python ingest.py --source sheets` reads from the live mock Google Sheet → same outputs
- [ ] Retrieval query `"delays in booking confirmation"` returns relevant chunks
- [ ] `ground_truth.json` exists with three labelled bottlenecks
- [ ] All scrub tests pass — no seeded name/email/phone/postcode in any output
- [ ] `generate.py` has zero imports shared with any pipeline or detection file
- [ ] `credentials/` is gitignored and absent from git history
- [ ] `data/real/` is gitignored
- [ ] No LLM API calls anywhere — all embeddings local

---

# [YOU] When Phase 1 is done — next conversation with Foyle

Once the pipeline reads from the live mock Google Sheet you have a demo worth showing:
- Open the Streamlit dashboard (built in parallel)
- Point it at the mock Sheet
- Show Foyle a live system reading from Google Sheets, detecting bottlenecks, and presenting recommendations for approval

That demo is the basis for the Option A / Option B conversation:
- **Option A:** Populate the mock Sheet with synthetic data shaped like Foyle's real process — show them what their data would look like in the system. No real data involved.
- **Option B (after Jose clears it):** Foyle exports one real tracker into the Sheet. Same pipeline, no code changes.
