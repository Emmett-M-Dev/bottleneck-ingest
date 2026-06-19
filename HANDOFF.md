# Phase 1 — Recap & Handoff

_Last updated: 2026-06-19_

## What this project is
`bottleneck-ingest` — Phase 1 of the SME bottleneck-detection dissertation system. It
reads SME ops data (local `.xlsx` + `.txt`, and a live Google Sheet), scrubs PII,
normalises to a canonical **event log**, and embeds into **ChromaDB** for retrieval.
Local and Sheets paths produce identical output, so the mock environment behaves like a
real deployment.

Separate repo from `hitl-interface` (the human-in-the-loop approval UI, already built) —
on purpose: this project uses ChromaDB / spaCy / torch, which `hitl-interface` forbids.

- No LLM API calls. Embeddings are local (all-MiniLM-L6-v2).
- All data synthetic. No PII reaches any output.

## Status: Phase 1 local path COMPLETE & verified ✅

`python ingest.py --source local` runs clean (exit 0):
```
[local]  Read 1001 event rows from 1 xlsx, 6 paragraphs from 3 txt files
Scrubbed: 818 entities replaced
Records:  1007 written to outputs\records.jsonl
Events:   1001 rows written to outputs\event_log.parquet
Embedded: 1007 chunks into ChromaDB collection 'sme_ops'
```

Verified against the brief's Definition of Done:
- [x] `--source local` → parquet + jsonl + ChromaDB
- [x] Retrieval `"delays in booking confirmation"` returns relevant chunks
- [x] `synthetic/ground_truth.json` exists with 3 labelled bottlenecks (BN001/002/003 = 88/66/35 cases)
- [x] Scrub audit: 0 seeded name/email/phone/postcode in any output
- [x] `generate.py` shares zero imports with pipeline/readers/scrub/detection
- [x] `credentials/` + `data/real/` gitignored
- [x] 17/17 tests pass (`pytest`)
- [ ] `--source sheets` → **blocked on Step 0 (YOU)**, see below

## What was built
| Area | Files |
|------|-------|
| Contract | `models.py` (Event, NormalisedRecord), `config.py` (paths, COLUMN_MAP, sheet IDs) |
| Synthetic data | `synthetic/generate.py` → `data/synthetic/bookings_tracker.xlsx` + 3 `ops_notes_*.txt` + `synthetic/ground_truth.json` |
| Readers | `readers/excel_reader.py`, `text_reader.py`, `sheets_reader.py` |
| Scrub | `scrub/anonymise.py` (spaCy NER + regex, stable placeholders) |
| Pipeline | `mapping.py`, `pipeline/normalise.py`, `chunk.py`, `embed.py` |
| CLI | `ingest.py` (`--source local|sheets|all`) |
| Tests | `tests/test_models.py`, `test_readers.py`, `test_scrub.py`, `test_pipeline.py` |

Data shape decision (locked with Emmett): **long event log** — one row per (case, stage)
event. 150 cases → ~1001 event rows.

## Environment — IMPORTANT (the hard part)
This Windows machine's default Python is **Anaconda-based**. Anaconda's bundled MKL/OpenMP
collides with pip-installed native wheels and causes `WinError 1114` (torch 2.12 /
onnxruntime) and **segfaults** (chroma Rust core, hnswlib, pyarrow) at scale.

Fixes applied (all baked into code/requirements):
1. **Venv built on clean standalone CPython via `uv`**, not Anaconda.
   `uv python install 3.11 && uv venv --python 3.11 .venv`
2. **All threading backends pinned to 1 thread** + `KMP_DUPLICATE_LIB_OK=TRUE`, set at the
   top of `ingest.py` and `pipeline/embed.py` before any native lib loads (torch+hnswlib
   OpenMP collision).
3. **fastparquet instead of pyarrow** — `import pandas` eagerly loads Arrow C++, which
   segfaults in-process with hnswlib. fastparquet writes the same `.parquet` without Arrow.

Pinned stack (see `requirements.txt`): torch 2.2.2, sentence-transformers 2.7.0,
spacy 3.7.5, chromadb 0.6.3 (+ onnxruntime 1.16.3 only to satisfy chroma's eager default
EF import), fastparquet, numpy 1.26.4, pandas 2.2.3.

→ On a fresh non-Anaconda machine, the standard `python -m venv` + `pip install` works fine.

## How to resume / run
```powershell
# from c:\Users\Emmet\bottleneck-ingest
.venv\Scripts\activate
python synthetic/generate.py        # regenerate synthetic data (already done)
pytest                              # 17 pass
python ingest.py --source local     # full pipeline, writes outputs/
```

## NEXT — Step 0 (YOU / Emmett): make `--source sheets` live
Mock Google account `foyle.mock.sme@gmail.com` already created. Remaining:
1. Upload `data/synthetic/bookings_tracker.xlsx` to that account's Google Drive →
   open with Google Sheets → copy the Spreadsheet ID from the URL.
2. Paste it into `config.py` → `SHEET_ID`.
3. Create a `ops-notes/` folder in Drive, upload the 3 `.txt` files.
4. Google Cloud Console → new project `foyle-mock-pipeline` → enable **Google Sheets API**
   + **Google Drive API**.
5. Credentials → OAuth 2.0 Client ID → **Desktop app** → download to
   `credentials/google_oauth.json` (gitignored — never commit).
6. `python ingest.py --source sheets` → browser opens once → log in to the mock account +
   Allow → `credentials/token.json` cached → later runs silent.

Expected: same output shape as `--source local`, sourced from the live Sheet.

## After Phase 1 — the demo
With `--source sheets` live, point the `hitl-interface` Streamlit dashboard at the mock
Sheet for a live demo: real system reading Google Sheets → detecting bottlenecks →
presenting recommendations for approval. Basis for the Option A / Option B conversation
with Foyle.

## Out of scope (not built — by design)
- Detection + RAG answer layers (consume `event_log.parquet` + Chroma; built later).
- Wiring the pipeline into the Streamlit dashboard (parallel/next integration).

## Git
Nothing committed yet — all untracked. Commit Phase 1 when ready.
