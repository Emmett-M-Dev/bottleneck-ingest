# bottleneck-ingest — Phase 1 ingestion pipeline

Reads SME operations data from local files **and** a live Google Sheet, scrubs PII,
normalises it to a canonical event log, and embeds it into ChromaDB for retrieval.
Both input paths produce identical output, so the downstream detection + RAG layers
cannot tell the mock environment from a real deployment.

All embeddings are computed **locally** (all-MiniLM-L6-v2). No LLM API calls anywhere.

## One-time setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

Smoke-check the install:

```powershell
python -c "import pandas, openpyxl, chromadb, sentence_transformers, spacy; from googleapiclient.discovery import build; print('all imports ok')"
```

### Generate the synthetic data

```powershell
python synthetic/generate.py
```

Writes `data/synthetic/bookings_tracker.xlsx`, three `ops_notes_*.txt`, and
`synthetic/ground_truth.json` (the injected-bottleneck answer key — never read by the
pipeline; the detector must rediscover the bottlenecks from `outputs/event_log.parquet`).

### Google Sheets setup (for `--source sheets`)

1. Upload `data/synthetic/bookings_tracker.xlsx` to Google Drive in the mock account,
   open it with Google Sheets, and copy the Spreadsheet ID from the URL
   (`docs.google.com/spreadsheets/d/THIS_PART/edit`).
2. Create a folder `ops-notes/` in Drive and upload the `.txt` files.
3. In Google Cloud Console: create project `foyle-mock-pipeline`, enable the
   **Google Sheets API** and **Google Drive API**, then
   Credentials → Create Credentials → OAuth 2.0 Client ID → **Desktop app**.
4. Download the credentials JSON to `credentials/google_oauth.json`.
5. Paste the Spreadsheet ID into `SHEET_ID` in `config.py`.
6. First run of `--source sheets` opens a browser — log in to the mock account and
   click Allow. A token is cached to `credentials/token.json`; later runs are silent.

## Running

```powershell
python ingest.py --source local     # reads data/synthetic/ (xlsx + txt)
python ingest.py --source sheets    # reads the live mock Google Sheet
python ingest.py --source all       # both, deduplicated by source_ref
```

Each mode: read → scrub → normalise → write `outputs/event_log.parquet` +
`outputs/records.jsonl` → embed into ChromaDB at `outputs/chroma/`
(collection `sme_ops`).

## Outputs (all under `outputs/`, gitignored)

| File | Contents |
|------|----------|
| `event_log.parquet` | One row per (case, activity, timestamp) event — the detector's input |
| `records.jsonl` | One scrubbed `NormalisedRecord` per line |
| `chroma/` | Persistent vector store, collection `sme_ops` |

## Pointing at a new data source

Edit **`config.py` only**:
- `COLUMN_MAP` — add the new source's column names as aliases for the canonical fields
  (`case_id`, `activity`, `timestamp`, `actor`, `status`).
- `SHEET_ID` / `SHEET_TAB` / `SHEET_RANGE` — for a different Sheet.

No reader, pipeline, or detection code changes.

## Tests

```powershell
pytest
```

Covers the data contract, readers (excel mapping + cleaning, text split, sheets parsing
against a mocked API), PII scrubbing (seeded name/email/phone/postcode must not appear in
any output; dates + numbers survive), and normalise/chunk.

## Troubleshooting

- **Segfault / `WinError 1114` during embedding on Windows:** the venv is likely built on
  an Anaconda Python, whose bundled MKL/OpenMP collides with the pip-installed native
  wheels (torch, chroma/hnswlib, pyarrow). Build the venv on a clean standalone CPython
  instead — e.g. `uv python install 3.11 && uv venv --python 3.11 .venv`. The code already
  pins all threading backends to a single thread and uses `fastparquet` (not pyarrow) to
  keep the Arrow C++ runtime out of the embedding process.

## Privacy / git hygiene

- `credentials/` and `data/real/` are **gitignored and never committed**.
- No raw PII reaches ChromaDB, `records.jsonl`, or `event_log.parquet` — scrubbing runs
  before anything is stored or embedded.
