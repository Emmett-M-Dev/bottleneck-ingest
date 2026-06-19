# Phase 1 — Full Build Report

**Project:** `bottleneck-ingest` — Phase 1 of the SME bottleneck-detection dissertation system
**Status:** ✅ COMPLETE & verified (local + live Google Sheets paths)
**Report date:** 2026-06-19
**Commits:** `5c2777b` (local path), `4cc76d1` (live sheets path)

---

## 1. What Phase 1 is

The ingestion + scrub + live-mock-environment foundation for the dissertation system.
It reads small/medium-enterprise (SME) operations data from two interchangeable
sources, removes all personally identifiable information (PII), normalises everything
to a single canonical **event log**, and embeds the text into a local **ChromaDB**
vector store for retrieval.

The two source paths produce **identical output shapes**, so the downstream detection
and RAG layers (built later, separate phases) cannot tell mock data from a real
deployment. That is the point: the mock Google environment makes the demo look like a
deployed system, because mechanically it is one — just on synthetic data.

**Two hard constraints, both honoured:**
- **No LLM API calls.** All embeddings are local (`all-MiniLM-L6-v2` via sentence-transformers).
- **No PII reaches any output.** Scrub runs before anything is written or embedded.

Separate repo from `hitl-interface` (the human-in-the-loop approval UI) on purpose:
this project depends on ChromaDB / spaCy / torch, which `hitl-interface` forbids.

---

## 2. Architecture & data flow

```
                ┌─────────────────────────────────────────────────────┐
   SOURCE A     │  readers/excel_reader.py   data/synthetic/*.xlsx     │
   (local)      │  readers/text_reader.py    data/synthetic/*.txt      │
                └─────────────────────────────────────────────────────┘
                                       │  raw dicts (original headers + _source_ref)
   SOURCE B     ┌─────────────────────────────────────────────────────┐
   (live)       │  readers/sheets_reader.py  live mock Google Sheet    │
                └─────────────────────────────────────────────────────┘
                                       ▼
                         mapping.py  (COLUMN_MAP: source header → canonical field)
                                       ▼
                  pipeline/normalise.py  → NormalisedRecord + Event rows
                                       │   (scrub/anonymise.py runs here, before storage)
                                       ▼
            ┌──────────────────────────┼──────────────────────────────┐
            ▼                          ▼                               ▼
   outputs/records.jsonl   pipeline/chunk.py → embed.py        outputs/event_log.parquet
   (full NormalisedRecords)  ChromaDB collection "sme_ops"     (one row per Event)
```

**Canonical contract** (`models.py`), agreed by every layer:
- `Event` — `case_id, activity, timestamp (ISO 8601), actor, status, source_ref`
- `NormalisedRecord` — `record_id, source_type, source_ref, ingested_at, text,
  structured, events[], scrubbed_entities[]`

Both are plain dataclasses with explicit `to_dict` / `from_dict` so they serialise to
JSONL / Parquet without a validation library.

---

## 3. Module-by-module

### `config.py` — single source of truth
Every path, model name, sheet ID, and column alias lives here; no magic strings
elsewhere. To point the pipeline at a **new** data source you edit `COLUMN_MAP` and the
`SHEET_*` values and nothing else.
- `EMBEDDING_MODEL = "all-MiniLM-L6-v2"`, `SPACY_MODEL = "en_core_web_sm"`
- `CHUNK_SIZE = 400`, `CHUNK_OVERLAP = 80`
- `SCRUB_ENTITIES = {PERSON, ORG, GPE, LOC, FAC, NORP}`
- `SHEET_ID` now points at the live mock Sheet; `SHEET_TAB = "Sheet1"`, `SHEET_RANGE = "A:Z"`
- `COLUMN_MAP` maps 5 canonical fields, each with 4 source-header aliases
  (e.g. `case_id` ← `Booking Ref | Case ID | Reference | ID`)

### `models.py` — the data contract
`Event` and `NormalisedRecord` dataclasses + round-trippable dict serialisation.
Tested both directions.

### `synthetic/generate.py` — synthetic data + ground truth
Simulates an educational-tourism SME (school-group bookings):
- **150 cases**, 4-month window (Jan–Apr 2026), seed `42` (reproducible)
- Six stages per case: `Enquiry → Quote → Booking Confirmation → Payment →
  Pre-Arrival Logistics → Completed`
- Three **known bottlenecks injected** and recorded in `ground_truth.json`:

| ID | Type | Injected | Affected cases |
|----|------|----------|----------------|
| BN001 | delay | Booking Confirmation 8–14 days (vs 1–2) | 88 |
| BN002 | repetition | Payment re-entered into Pre-Arrival sheet | 66 |
| BN003 | rework | Quote revised (loop-back) before confirm | 35 |

Outputs the messy tracker `bookings_tracker.xlsx` (mixed date formats, blank cells,
inconsistent capitalisation — deliberately realistic) plus 3 `ops_notes_*.txt` files.
**Total event rows: 1001.**

**Critical isolation rule (honoured):** `generate.py` shares zero imports with any
pipeline/reader/scrub/detection code. The detector must rediscover the bottlenecks from
`event_log.parquet` alone — never from the answer key.

### `readers/excel_reader.py`
Reads any `.xlsx` in a folder with pandas + openpyxl (`dtype=str` to preserve mixed
date formats as strings). Validates up front that headers **can** map and raises a clear
`ValueError` if none match — never silently drops data. Blank cells → `None`, whitespace
stripped. `_source_ref = "{filename}:{row}"` (data starts at spreadsheet row 2).

### `readers/text_reader.py`
Reads any `.txt`, splits on blank line (`\n\n`) into paragraphs, returns
`[{"text", "source_ref"}]`. No structure extraction — feeds RAG as-is after scrub.

### `readers/sheets_reader.py`
Reads the live mock Sheet via the Google Sheets API. Returns the **same dict shape** as
the excel reader (original headers + `_source_ref = "sheets:{SHEET_ID}:{row}"`), so the
pipeline cannot distinguish a local file from the live Sheet.
- `get_credentials()`: loads cached `token.json`; refreshes if expired; otherwise runs
  the OAuth installed-app browser flow once and caches the token.
- Scope: `spreadsheets.readonly` (read-only — the pipeline never writes back).
- Short rows padded; blank strings → `None`.
- Lazily imported in `ingest.py` so local mode needs no Google credentials.

### `mapping.py` — the one place headers become canonical
`resolve_columns()` (case-insensitive, whitespace-tolerant) + `map_row()`. Used by both
the excel reader (validate + fail fast) and `normalise.py` (extract), so alias logic
lives in exactly one place. `_`-prefixed keys (e.g. `_source_ref`) are ignored.

### `scrub/anonymise.py` — PII removal (runs before any storage)
- spaCy NER replaces PERSON/ORG/GPE/LOC/FAC/NORP with **stable placeholders**
  (`[PERSON_1]`, `[ORG_1]`…). Same original → same placeholder within a document.
- Regex catches **emails, UK phone numbers, UK postcodes**.
- Dates and numbers pass through unchanged (not in `SCRUB_ENTITIES`; regexes don't match).
- Greedy non-overlapping span selection (earliest start, then longest) merges regex +
  NER hits without double-substitution.
- Returns `(scrubbed_text, [{type, placeholder}])`. **Originals are never returned or stored.**
- spaCy pipeline loads with `lemmatizer/tagger/parser` disabled (NER only — faster).

**Scrub policy nuance** (`normalise.py`): structured rows only scrub the `actor` field
(staff names). Structural fields — `case_id, activity, timestamp, status` — are
controlled vocabulary carrying no PII and are deliberately **not** scrubbed, because
running NER over them would corrupt the activity labels the detector depends on (e.g.
`"Booking Confirmation"` must survive verbatim). Free-text ops notes are fully scrubbed.

### `pipeline/normalise.py`
Raw dicts → `NormalisedRecord` + `Event`. One record per row; one `Event` only where
`case_id + activity + timestamp` are all present (incomplete rows are **logged, not
crashed**). Timestamps parsed to ISO 8601 (`dayfirst=True`); unparseable values keep
their original string rather than being dropped. `record_id = source_ref` for structured
rows, `"{source_ref}#p{idx}"` for text paragraphs.

### `pipeline/chunk.py`
Splits `NormalisedRecord.text` into overlapping chunks (size/overlap from config),
preserving `record_id, source_ref, source_type, ingested_at, chunk_index` in metadata so
any retrieved chunk traces back to its source.

### `pipeline/embed.py`
Loads `all-MiniLM-L6-v2` locally, persistent ChromaDB client, upserts into collection
`"sme_ops"`. **Deterministic chunk IDs** `"{record_id}#c{chunk_index}"` — re-running
ingestion **replaces** chunks in place rather than duplicating. `normalize_embeddings=True`.
Also exposes `query()` for retrieval.

### `ingest.py` — CLI
`--source local | sheets | all`. Each mode: read → scrub → normalise → write jsonl →
embed → write parquet, then prints a summary. `all` concatenates both and dedups by
`record_id`.

**Ordering constraint baked in:** jsonl + embed (chroma/hnswlib) run **before** the
parquet write, because pyarrow's Arrow C++ runtime cannot share the process with the
HNSW index build on this machine without segfaulting.

---

## 4. The environment battle (the genuinely hard part)

This Windows machine's default Python is Anaconda-based. Anaconda's bundled MKL/OpenMP
collides with pip-installed native wheels — producing `WinError 1114` (DLL load failures
in torch / onnxruntime) and **segfaults** (chroma's Rust core, hnswlib, pyarrow) at scale.

Fixes, all baked into code + `requirements.txt`:

1. **Clean standalone CPython via `uv`**, not Anaconda:
   `uv python install 3.11 && uv venv --python 3.11 .venv`
2. **All threading backends pinned to 1 thread** + `KMP_DUPLICATE_LIB_OK=TRUE`, set at
   the very top of `ingest.py` and `pipeline/embed.py` **before any native lib loads**
   (resolves the torch + hnswlib/blis OpenMP collision).
3. **fastparquet instead of pyarrow** — `import pandas`'s eager Arrow C++ load segfaults
   in-process with chroma/hnswlib + torch. fastparquet writes the same `.parquet` without Arrow.
4. **chromadb 0.6.3** (stable C++/hnswlib core) — chromadb 1.x's Rust core segfaults here.
5. **onnxruntime 1.16.3** pinned — only needed so chroma's default embedding function
   imports cleanly; never actually invoked (we always pass our own embeddings).

Pinned stack: torch 2.2.2, sentence-transformers 2.7.0, transformers 4.41.2,
huggingface-hub 0.23.4, spacy 3.7.5, chromadb 0.6.3, onnxruntime 1.16.3, fastparquet,
numpy 1.26.4, pandas 2.2.3, google-api-python-client 2.137.0.

> On a fresh non-Anaconda machine, standard `python -m venv` + `pip install` works fine —
> these pins are a Windows/Anaconda-coexistence fix, not a general requirement.

---

## 5. Live Google Sheets enablement (this session)

The mock Google account (`foyle.mock.sme@gmail.com`) + OAuth desktop client were set up
manually. Enabling `--source sheets` this session:

1. **Pre-verified** `sheets_reader.py` against the contract — 4/4 reader tests pass
   (including a mocked-API parse test) before any live run.
2. **First live run failed** with `HttpError 400: "The document must not be an Office
   file."` — the `SHEET_ID` pointed at the raw uploaded `.xlsx`, which the Sheets API
   refuses. **Fix:** open the xlsx in Drive → *Save as Google Sheets* (a separate native
   Sheet with a different ID) → paste that ID into `config.py`.
3. **Second run succeeded** — 1001 rows read from the live Sheet into the same
   parquet/jsonl/Chroma outputs as `--source local`. Auth cached to `token.json`;
   reruns are silent (no browser).
4. **Retrieval verified** — query `"delays in booking confirmation"` returns Booking
   Confirmation chunks, including a `sheets:…`-sourced chunk (proof the live path is
   embedded and queryable).

### `--source all` finding (important for the demo)
Dedup keys on `record_id`, and `record_id = source_ref`. Local refs (`xlsx:N`) and sheets
refs (`sheets:ID:N`) **never collide**, so with mirrored mock data `--source all`
**double-counts** (2008 records / 2002 events — every booking twice). It is only
meaningful when local and sheets hold *different* data. **For the demo, use a single
source (`--source sheets`).**

### Final clean state
The Chroma collection was wiped and rebuilt from sheets only:
**1001 chunks, `source_type = sheets` exclusively.** Single canonical source, demo-ready.

---

## 6. Verification — Definition of Done

| Requirement | Status |
|---|---|
| `--source local` → parquet + jsonl + ChromaDB | ✅ |
| `--source sheets` → live mock Sheet → same output shape | ✅ |
| Retrieval `"delays in booking confirmation"` returns relevant chunks | ✅ |
| `ground_truth.json` with 3 labelled bottlenecks (88/66/35) | ✅ |
| Scrub: 0 seeded name/email/phone/postcode in any output | ✅ |
| `generate.py` shares zero imports with pipeline/readers/scrub/detection | ✅ |
| `credentials/` + `data/real/` gitignored, absent from history | ✅ |
| No LLM API calls — all embeddings local | ✅ |
| Test suite | ✅ 17/17 pass |

Tests: `tests/test_models.py` (dataclass round-trip), `test_readers.py` (excel mapping +
cleaning, text split, mocked sheets parse), `test_scrub.py` (seeded PII absent, dates/
numbers preserved), `test_pipeline.py`.

---

## 7. Git

| Commit | Contents |
|---|---|
| `5c2777b` | Phase 1 local path — 29 files, full pipeline + synthetic data + tests |
| `4cc76d1` | Live Google Sheets path — `SHEET_ID` wired, HANDOFF updated, brief added |

`credentials/`, `data/real/`, `outputs/`, `.venv/` gitignored. No credentials in history
(verified before each commit).

---

## 8. Known limitations / notes for later

- **`--source all` double-counts** mirrored data (see §5). Use single-source for the demo.
- **`ops-notes/` Drive folder is not read by sheets mode.** `sheets_reader` reads only the
  structured Sheet; the `.txt` notes are consumed only in `--source local`. If the live
  demo needs the notes too, a Drive-text reader would be a small addition.
- **chroma 0.6.3 telemetry** logs a harmless `capture() takes 1 positional argument`
  error on every run — cosmetic, not a failure.
- A `dayfirst` pandas `UserWarning` appears on date parsing — harmless.

---

## 9. What's next (outside this repo)

- **The demo:** point the `hitl-interface` Streamlit dashboard at the live mock Sheet —
  a real system reading Google Sheets → detecting bottlenecks → presenting recommendations
  for approval. Basis for the Foyle Option A / Option B conversation.
- **Detection + RAG layers:** consume `event_log.parquet` + the Chroma collection;
  rediscover BN001–003 independently. Separate phase/repo.

Phase 1 is closed.
