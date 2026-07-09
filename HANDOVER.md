# Handover — SME Bottleneck Detection with Human-in-the-Loop

_Last updated: 2026-07-09 · feature-freeze target ~2026-08-01 · dissertation due 2026-08-24_

## 1. What this is

A dissertation system that detects operational **bottlenecks** in an SME's messy
spreadsheet "drive" and surfaces them to a human through two **human-in-the-loop
(HITL) gates**. The thesis claim is **generalisability**: the same core pipeline
onboards a new SME by adding a config block and a mapping — *no new reader or
detector code*. Proven with two contrasting SMEs (educational-placement +
joinery) through the identical pipeline.

Two moving parts distinguish it from a plain ETL:

1. **Mapping-inference agent** (Claude API) inspects a messy drive and proposes
   how each file/column maps to a fixed canonical schema, plus a "mess report".
2. **Two HITL gates** — gate #2 approves that *mapping* before ingestion; gate #1
   approves each *bottleneck fix*. Corrections + timing are logged: those are the
   measurable HITL numbers in the dissertation.

## 2. Repos (both on `master`, committed & clean)

| Repo | Path | Role |
|---|---|---|
| `bottleneck-ingest` | `c:\Users\Emmet\bottleneck-ingest` | Python pipeline: audit → ingest → detect → export → remediate → eval. Own `.venv`. |
| `hitl-react` | `c:\Users\Emmet\hitl-react` | React 18 + Vite + Tailwind + @xyflow/react dashboard. FastAPI backend in `api/` (own `.venv`, py3.14). Vite proxies `/api` → `:8000`. |
| `hitl-interface` | `c:\Users\Emmet\hitl-interface` | Legacy Streamlit UI + providers/fixtures. **Superseded by hitl-react** for the mapping/dashboard flow; kept for provider abstractions + fixtures. |

The API is a **thin orchestrator**: it shells out to the pipeline's own venv
(`.venv/Scripts/python.exe`) because `hitl-react/api` has no pandas/chroma.

## 3. Architecture — the canonical schema is the contract

```
messy drive (data/synthetic/messy_<profile>/*.xlsx)
      │
      ▼  audit/  ── Claude inspects headers + scrubbed sample rows (ZERO raw PII)
outputs/ui_mapping_proposal_<profile>.json   { per-file role + per-column canonical field + mess_report }
      │
      ▼  [HITL GATE #2 — Mapping Review tab] human edits roles/columns → Approve
mappings/approved_<profile>.json             (+ mapping_decisions.jsonl = HITL metrics)
      │
      ▼  ingest.py --source messy --profile <p>   (HARD ERROR if no approved mapping)
readers/mapped_reader.py → normalise → records.jsonl → ChromaDB → event_log.parquet
      │
      ▼  detection/detect.py  detect_generic(delay/repetition/rework markers)
      ▼  bridge/export_messy.py
outputs/ui_cases.json + ui_workflow.json     → dashboard tabs
      │
      ▼  [HITL GATE #1 — Fixes tab] human approves each fix
      ▼  remediate/  AI status-normalisation → cleaned copies (originals untouched) + diff
      ▼  eval/score_mapping.py  → the dissertation numbers
```

**Canonical layer** = `Event` (case_id / activity / timestamp / actor / status /
source_ref) + `NormalisedRecord`. Everything per-SME is a thin adapter. Adding an
SME = a `MESSY_PROFILES` config block + an approved mapping. That is the whole
generalisability argument.

## 4. Key files

**bottleneck-ingest**
- `config.py` — `MESSY_PROFILES` (markers, stage_order, ground-truth paths, `ui` branding block per SME).
- `audit/` — `scan.py` (headers + ≤5 scrubbed sample rows), `infer.py` (only module touching the Anthropic SDK; `messages.parse`, no temperature — 400s on Opus 4.8), `propose.py` (heuristic baseline + LLM), `run.py` (CLI).
- `readers/mapped_reader.py` — generic connector: approved mapping + drive → canonical rows; dedup on (case_id, activity, timestamp) keep-first (neutralises overlapping seasonal fork).
- `detection/detect.py` — `detect_generic(df, delay_stage, repetition_stage, rework_stage, delay_threshold_days)`. **Exactly 3 detector types.**
- `bridge/export_messy.py` — builds `ui_cases.json` + `ui_workflow.json`; nodes carry `sources` (which sheets feed each stage).
- `remediate/` — `scan.py`/`propose.py`/`apply.py`/`run.py`; status freetext → controlled vocab `{Complete, Open, N/A}`; cleaned copies to `messy_<profile>_cleaned/`.
- `eval/score_mapping.py` — baseline vs LLM vs human-approved scoring.
- `synthetic/generate_messy_foyle.py`, `generate_messy_joinery.py` — seeded synthetic drives + ground truth.

**hitl-react**
- `api/main.py` — endpoints: `/api/cases`, `/api/workflow`, `/api/mapping-proposals`, `/api/mapping-approvals`, `/api/remediation/{p}` (+`/apply`), `/api/profiles` (+`/{p}/activate`). Per-profile export cache + `active_profile.txt` pointer → instant SME switch. Remediation plan auto-regenerates when event log / mapping newer than plan.
- `src/App.jsx` — top-level state; `approvedMap` keyed by proposal `generated_at` (fresh audit reopens the gate).
- `src/components/mapping/` — MappingTab, MappingCard (gate #2 editor).
- `src/components/dashboard/WorkflowDAG.jsx` — hover a stage → source sheet(s).
- `src/components/fixes/` — FixesTab, HITLCard (gate #1), RemediationPanel.
- `src/components/layout/ProfileSwitcher.jsx` — click the SME brand to switch.

## 5. How to run

**Pipeline (bottleneck-ingest), from repo root, PowerShell — use the venv python explicitly:**
```
$env:PYTHONIOENCODING="utf-8"           # ✔ chars in status values are cp1252-hostile
.venv/Scripts/python.exe -m audit.run --profile foyle [--offline]   # --offline = free, no API
#   → review + Approve in the dashboard Mapping Review tab (writes mappings/approved_foyle.json)
.venv/Scripts/python.exe ingest.py --source messy --profile foyle   # errors if no approved mapping
.venv/Scripts/python.exe -m bridge.export_messy --profile foyle
.venv/Scripts/python.exe -m remediate.run --profile foyle [--apply]
.venv/Scripts/python.exe -m eval.score_mapping --profile foyle
```
Swap `--profile joinery` — **zero new code** — for the second SME (the thesis point).

**Dashboard (hitl-react):**
```
# terminal 1 — API
cd hitl-react/api ; .venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
# terminal 2 — UI
cd hitl-react ; npm run dev          # http://localhost:5173
```
Profiles switch instantly once each has been built once (cache + pointer).

## 6. Eval numbers (dissertation headline)

Column-mapping **F1**, baseline (heuristic) → LLM → human-approved:

| Profile | baseline | LLM | human |
|---|---|---|---|
| foyle | 0.846 | 0.968 | 1.000 |
| joinery | 0.308 | 0.909 | 1.000 |

Baseline collapses on joinery's renamed-header fork (`Job#/Phase/When/Who`) —
that gap is the argument for the LLM audit. Human gate closes the residual
(foyle: 1 correction; joinery: 2). Full JSON: `outputs/eval_mapping_<profile>.json`.

## 7. Current state (2026-07-09)

- **All milestones M0–M6 shipped.** Two profiles run end-to-end through one pipeline.
- Both repos committed & clean on `master`. Latest: `c861945` (ingest), `5274db9` (react).
- Foyle drive back to its **reproducible 5-file** state (2 ad-hoc test sheets dropped this session; pipeline unwound to 114 events / 18 cases; `mappings/approved_*.json` browser-drift reverted).
- Tests green (`pytest -q`): mapping/remediation/detection/ground-truth suites pass.

## 8. Gotchas & constraints (read before touching)

1. **No `temperature` / sampling params on Opus 4.8** — 400 error. Determinism comes from schema + closed vocab. Model id `claude-opus-4-8`.
2. **Windows native-lib segfaults** — `audit/` and `remediate/` must NOT import chromadb/pyarrow/torch. They run as separate processes with a thread-pin env block.
3. **`PYTHONIOENCODING=utf-8`** required — status values contain `✔` (✔); cp1252 default raises `UnicodeEncodeError`.
4. **Zero raw PII to the API** — every sample cell passes through `scrub.anonymise` before the audit payload. There is a test asserting this.
5. **`ANTHROPIC_API_KEY` in `bottleneck-ingest/.env`** (gitignored). The key was pasted in chat during development — **rotate it** at the Anthropic console before submission.
6. **Mapping drift** — approving in the browser overwrites `mappings/approved_<profile>.json`. If eval numbers shift unexpectedly, `git checkout mappings/approved_*.json` to restore the committed mappings.
7. **Bottleneck count is fixed at 3 per profile by design** (delay / repetition / rework detectors). More data bumps `affected` counts; it does not spawn new bottleneck *types*. A 4th requires a new detector + seeded synthetic pattern.
8. **Windows `python` shim** — call `.venv/Scripts/python.exe` explicitly; bare `python` may hit the Store stub.

## 9. What's next (candidates, none blocking)

- **Write-up** — system is feature-complete; the honest default is to pivot to the dissertation.
- **Deliberate 3rd foyle fork** — if a richer demo is wanted, add it *properly* in `generate_messy_foyle.py` + ground truth (not as leftover test files).
- **Bespoke Mapping / Bottlenecks tab layouts** — currently token-reskin only.
- **Post-freeze stretch** — Drive-listing wrapper for `audit/scan.py` (real SharePoint/Drive crawl); LLM-proposed freetext status value-maps.

Cut order if time is short: Drive wrapper → value-maps → model sweep. Non-cuttable
(carry thesis claims): both profiles, gate-#2 metrics, eval script.
