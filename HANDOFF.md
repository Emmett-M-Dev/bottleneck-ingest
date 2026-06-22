# SME Streamliner — Recap & Handoff

_Last updated: 2026-06-22_

Two repos, one system. This file lives in `bottleneck-ingest` but covers both.

```
bottleneck-ingest   heavy backend: ingest → scrub → detect → embed → export   (chromadb/spacy/torch)
hitl-interface      Streamlit dashboard: reads the export, HITL approval        (NO heavy libs — JSON only)
```
They are split on purpose. The only thing crossing between them is plain JSON
(`ui_cases.json` + `ui_workflow.json`). No LLM API calls anywhere. All data synthetic.

## Status: working end-to-end ✅
Live Google Sheet → detection → RAG → Foyle-branded dashboard with HITL approval.

- `bottleneck-ingest`: 23/23 tests pass.
- `hitl-interface`: 19/19 tests pass.

## The pipeline (bottleneck-ingest)
`ingest.py --source local|sheets|all`: read → **scrub PII** → normalise to a canonical
**event log** → embed into ChromaDB. Writes `outputs/{records.jsonl, event_log.parquet, chroma/}`.

- **Contract:** `models.py` (Event, NormalisedRecord), `config.py` (paths, COLUMN_MAP, sheet IDs, detection thresholds, STAGE_ORDER).
- **Readers:** `readers/{excel,text,sheets}_reader.py`. Sheets path is live against the mock Google account.
- **Scrub:** `scrub/anonymise.py`. spaCy NER + regex, stable placeholders.
  - ⚠️ **Actor field is masked wholesale** via `scrub_actor()` + a process-wide registry
    (`reset_actor_registry()` per run). Context-free NER silently missed some bare names
    (leaked `Priya Patel`); wholesale masking guarantees zero leak + stable per-person tokens.
- **Detection:** `detection/detect.py` — finds the 3 seeded bottlenecks in `event_log.parquet`.
  Domain is the Foyle student-placement workflow (host families / CVs / invoices):
  | id | type | marker | accuracy vs ground truth |
  |----|------|--------|--------------------------|
  | BN001 | delay | Booking Confirmed gap ≥ 7 days (host-family matching) | P=0.88 / R=0.97 |
  | BN002 | repetition | a Document Re-request step exists (CV/ML/consent re-keyed) | P=1.00 / R=1.00 |
  | BN003 | rework | a Placement Re-allocation step exists (host fell through) | P=1.00 / R=1.00 |
- **Bridge:** `bridge/export_cases.py` → writes two files the dashboard reads:
  - `outputs/ui_cases.json` — list of `BottleneckCase` (matches the dashboard schema):
    real metrics + scrubbed evidence excerpts + ChromaDB retrieval (RAG) + authored fix template.
  - `outputs/ui_workflow.json` — `{nodes, edges, kpis}` for the workflow map.

## The dashboard (hitl-interface)
Streamlit, Foyle-branded, 3 tabs. Reads the JSON export — never imports chromadb/spacy/torch.

- `app.py` — names the concrete provider in `_get_provider()` only. Prefers
  `FileDataProvider` (live export), falls back to `MockDataProvider` (fixtures).
- `providers/file_provider.py` — reads `ui_cases.json` (+ `load_workflow()` for `ui_workflow.json`).
  Resolves via `$BOTTLENECK_CASES_PATH` → sibling `../bottleneck-ingest/outputs/ui_cases.json`.
- `theme.py` — Foyle navy/teal CSS. `components/`:
  - `header.py` — branded header + live KPI chips + ticker.
  - `workflow_map.py` — stage-flow map as a Graphviz DOT string (Streamlit renders it
    client-side → **no graphviz binary / extra dep**). Bottleneck stages flagged.
  - `bottlenecks.py` — styled severity cards.
  - `fixes.py` — **HITL core**: editable steps, RAG match table, approve/reject with an
    animated agent run-log. Approve/modify/reject append a real `Decision` to
    `logs/decisions.jsonl` (time-to-decision tracked). **Fixes are never executed.**

## Run the full demo
```powershell
# 1. bottleneck-ingest  (c:\Users\Emmet\bottleneck-ingest)
.venv\Scripts\activate
python ingest.py --source sheets        # live Sheet -> parquet + jsonl + Chroma
python -m bridge.export_cases           # -> outputs/ui_cases.json + ui_workflow.json

# 2. hitl-interface  (c:\Users\Emmet\hitl-interface, separate venv)
.venv\Scripts\activate
streamlit run app.py                    # Foyle dashboard, 3 tabs, 🟢 Live banner
```

## Environment — the hard part (Windows / Anaconda)
Default Python here is Anaconda; its MKL/OpenMP collides with pip wheels → `WinError 1114`
+ segfaults. Fixes baked into `bottleneck-ingest`:
1. Venv built on clean standalone CPython via `uv` (not Anaconda).
2. All threading backends pinned to 1 thread + `KMP_DUPLICATE_LIB_OK=TRUE`, set before
   any native lib loads (`ingest.py`, `pipeline/embed.py`).
3. **fastparquet, not pyarrow** — Arrow C++ segfaults in-process with hnswlib.
4. jsonl + embed run **before** the parquet write (ordering matters).

`hitl-interface` has none of this — it's just `streamlit / pandas / pydantic / pytest`.

## Git state
- `bottleneck-ingest` (branch master): `d4ebcce` workflow export · `d03599d` detection+bridge ·
  `00a36ee` actor-scrub fix · earlier Phase 1 commits.
- `hitl-interface` (branch main): `2ba8af5` Foyle restyle · `1171659` FileDataProvider wiring ·
  `813f468` initial app.
- Secrets gitignored in ingest: `credentials/`, `data/real/`, `outputs/`. Verified no PII in output.

## What's left / known limitations
- **Detection delay precision 0.88** — ~12 false positives from natural multi-day gaps.
  Tighten threshold/metric if cleaner numbers wanted.
- **`retrieved_resolutions` = corpus chunks, not curated prior resolutions** — no resolution
  corpus exists; retrieval returns semantically similar event lines. Honest for demo; flag in writeup.
- **Suggested fixes are authored templates** per pattern (the "no LLM API" constraint stands).
- **Domain: Foyle student-placement** — synthetic data + detection markers + bridge prose are now in
  Foyle's domain (Request Received → Placement Offer → Placement Re-allocation → Booking Confirmed →
  Invoice Issued → Document Re-request → Pre-Arrival Logistics → Arrival). Generated by
  `synthetic/generate.py` (1:1 relabel of the old generic stages; mechanics unchanged). All names /
  schools / host families / emails / phones are synthetic — modelled on the real SMMP schema, never
  its data.
  - **Live mock Sheet not yet repainted** — `synthetic/generate.py` also writes
    `data/synthetic/bookings_tracker_sheet.csv` (1001 rows, same columns as the Sheet). The live mock
    Sheet (`foyle.mock.sme@gmail.com`, `SHEET_ID` in config) still holds the old generic rows: to make
    the `--source sheets` path match, paste-replace Sheet1 with that CSV manually. The `--source
    local|all` path is already fully Foyle.
  - **Dashboard mock fallback still generic** — `hitl-interface/fixtures/cases.json` (MockDataProvider)
    and the `workflow_map.py` caption ("booking workflow") are unchanged; they only show when no live
    export exists. Reskin later if full parity wanted.
- **Export is two manual commands** — not folded into the CLI (kept separate to avoid the
  Arrow/hnswlib segfault ordering risk).
- **Run-log is cosmetic** — records a decision, never executes a fix (by design).
- **Activity feed / animated map** from the prototype are not 1:1 (Streamlit isn't JS) — the map is
  a static graphviz; no live ticker animation.

## Out of scope (by design)
- Executing approved fixes.
- LLM-generated fix prose.
