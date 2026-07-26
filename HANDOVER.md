# Handover — SME Bottleneck Detection with Human-in-the-Loop

_Last updated: 2026-07-23. Feature development is deliberately continuing; the
earlier feature-freeze note no longer applies._

> **Start here for a demo:** `DEMO.md` has the full copy-pasteable walkthrough
> across all three SMEs, including the outcome-measurement loop.

## 0. What changed most recently (2026-07-23)

The system grew an **action layer**. The product is no longer "upload
spreadsheets → read an AI interpretation → approve a recommendation"; it is an
operational action queue: what needs attention today, which cases, why, what it
costs, what to do, who owns it, by when — and, after a later analysis, whether
it worked.

Three things to know before touching anything:

1. **`actions/`** is the new generic layer (models, lifecycle, ranking, store,
   category routing). It is SME-agnostic and imports nothing heavy.
2. **Execution is routed by action category.** Only a machine-safe
   *data-quality* fix reaches the remediation executor. Approving a case action
   or a process change creates tracked work and touches no files. This
   corrected a real bug where any approval ran status normalisation.
3. **Approval no longer creates trusted knowledge.** An approved fix becomes an
   Intervention with a baseline; it must be completed, measured against a
   *later* analysis, and confirmed by a human before it enters the RAG store.

A third SME profile, **`advisory` (Northstar Advisory)** — a professional-services
lead-to-cash workflow — is the new commercial demo.

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
- `actions/` — the generic action layer. `models.py` (ActionItem, Intervention,
  InterventionOutcome, BusinessImpact, EvidenceReference, AnalysisSnapshot),
  `lifecycle.py` (the state machine + `is_trusted` gate), `build.py`
  (findings → evidence-backed items), `impact.py` (money/time/capacity, with the
  arithmetic in words), `templates.py` (per-finding-type recommendation + its
  CATEGORY — the routing decision), `rank.py` (deterministic, explainable),
  `execute.py` (approval, routing, the narrow machine-execution path),
  `outcome.py` (baseline vs observed, human validation), `store.py` (JSON
  persistence + merge that preserves a worker's edits), `cli.py` (the API's
  entry point; one JSON object per command).
- `detection/case_rules.py` — six generic case-level rules (SLA breach, stalled,
  unowned, unrealised value, overloaded owner, key-person dependency), all
  driven by `MESSY_PROFILES[<p>]["case_rules"]`.
- `bridge/export_actions.py` — builds + ranks the queue, writes the dashboard
  read model, appends the analysis snapshot, optionally runs the outcome review.
- `config.py` — `MESSY_PROFILES` (markers, stage_order, ground-truth paths, `ui`
  branding, `costs`, and the new `actions` + `case_rules` blocks per SME).
- `audit/` — `scan.py` (headers + ≤5 scrubbed sample rows), `infer.py` (only module touching the Anthropic SDK; `messages.parse`, no temperature — 400s on Opus 4.8), `propose.py` (heuristic baseline + LLM), `run.py` (CLI).
- `readers/mapped_reader.py` — generic connector: approved mapping + drive → canonical rows; dedup on (case_id, activity, timestamp) keep-first (neutralises overlapping seasonal fork).
- `detection/dynamic.py` — `detect_dynamic(df, stage_order)`: statistical scan of every stage (outlier gaps / duplicate entries / backward loops), **0..N findings, no marker config**. `detection/detect.py`'s `detect_generic` is now the eval-only baseline.
- `detection/anomaly.py` + `pipeline/llm.py` — advisory anomaly pass on a local Ollama model (aggregate stats only; skips silently when Ollama absent).
- `pipeline/learn.py` — learning loop: approved Gate-2 fixes → `data/learned/` + the `sme_resolutions` collection (fired by the API on POST /api/decisions).
- `eval/score_detection.py` — marker baseline vs dynamic detector P/R/F1 (macro-F1 0.52 → 1.00 both profiles).
- `bridge/export_messy.py` — builds `ui_cases.json` + `ui_workflow.json`; nodes carry `sources` (which sheets feed each stage).
- `remediate/` — `scan.py`/`propose.py`/`apply.py`/`run.py`; status freetext → controlled vocab `{Complete, Open, N/A}`; cleaned copies to `messy_<profile>_cleaned/`.
- `eval/score_mapping.py` — baseline vs LLM vs human-approved scoring.
- `synthetic/generate_messy_foyle.py`, `generate_messy_joinery.py` — seeded synthetic drives + ground truth.

**hitl-react**
- `api/main.py` — endpoints: `/api/cases`, `/api/workflow`, `/api/mapping-proposals`, `/api/mapping-approvals`, `/api/remediation/{p}` (+`/apply`), `/api/profiles` (+`/{p}/activate`), and the action loop: `GET /api/actions/{p}`, `POST /api/actions/{p}/{id}/decision`, `POST /api/actions/{p}/{id}/progress`, `POST /api/actions/{p}/review`, `GET /api/interventions/{p}`, `POST /api/interventions/{p}/{id}/validate`. Per-profile export cache + `active_profile.txt` pointer → instant SME switch. Remediation plan auto-regenerates when event log / mapping newer than plan.
- `src/components/today/` — **TodayTab** (the primary view), **ActionCard**
  (expandable evidence, owner/due-date, approve/reject/dismiss, progress),
  **InterventionBoard** (baseline vs projected vs now, outcome validation),
  **ImpactStrip**. `src/hooks/useActionQueue.js` fetches the queue; refresh is
  explicit so it never moves under a worker mid-decision.
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
Swap `--profile joinery` or `--profile advisory` — **zero new code** — for the
other two SMEs (the thesis point).

**The action queue (the worker-facing loop):**
```
.venv/Scripts/python.exe -m bridge.export_actions --profile advisory [--review]
.venv/Scripts/python.exe -m actions.cli decide   --profile advisory --action-id ACT-... --decision approve --owner "Niamh Foy" --due-date 2026-07-28
.venv/Scripts/python.exe -m actions.cli progress --profile advisory --action-id ACT-... --status completed
.venv/Scripts/python.exe ingest.py --source messy --profile advisory --drive data/synthetic/messy_advisory_followup
.venv/Scripts/python.exe -m bridge.export_actions --profile advisory --review
.venv/Scripts/python.exe -m actions.cli validate --profile advisory --intervention-id INT-... --effective yes
.venv/Scripts/python.exe -m pipeline.learn --profile advisory --promote
```
Every `actions.cli` subcommand prints exactly one JSON object on stdout — that
is the contract the FastAPI layer parses. Artefacts land in
`outputs/actions_<p>.json` (the store the worker owns),
`outputs/ui_actions_<p>.json` (the dashboard read model, disposable) and
`outputs/snapshots_<p>.jsonl` (one measurable state per analysis).

**Longitudinal replay (the "dynamic system" eval — eval-side only, pipeline core untouched):**
```
.venv/Scripts/python.exe synthetic/generate_stream.py --profile foyle   # 9 weekly snapshots + per-tick GT
.venv/Scripts/python.exe -m eval.replay --profile foyle [--llm]         # tick loop; default offline + fresh reset
.venv/Scripts/python.exe -m eval.plot_replay --profile foyle            # 3 PNGs -> outputs/
```
Replays the drive as it looked each week through the unchanged core: detection is
re-scored per tick against a *moving* ground truth, and a **simulated (oracle)
Gate-2 approver** feeds approvals to the learning loop so tick t+1 retrieves the
fix approved at tick t (learned-hit rate 0 → 1 over the run — the learning-loop
curve). Replay-learned entries live in `outputs/replay_learned_<p>.json`
(RES-RPL ids) and its gate log in `outputs/replay_decisions_<p>.jsonl` — the
dashboard's real learned file and decisions.jsonl are never touched. The default
fresh reset rebuilds `sme_resolutions` from the seeded corpora for reproducibility.

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
7. **Bottleneck count is DYNAMIC** — `detect_dynamic` returns 0..N structural findings; `detection/case_rules.py` adds 0..N case-level ones on top. `markers` in config are eval-only.
8. **Windows `python` shim** — call `.venv/Scripts/python.exe` explicitly; bare `python` may hit the Store stub.
9. **Subprocess stdout must be decoded as UTF-8 explicitly.** `subprocess.run(..., text=True)` decodes with the Windows ANSI codepage and raises `UnicodeDecodeError` on the `✔` in SME status values — which took down the API's action endpoints until fixed. Pass `encoding="utf-8", errors="replace"` alongside `PYTHONIOENCODING=utf-8` in the child env. Both are needed: the env var controls what the child *writes*, the encoding argument controls how the parent *reads*.
10. **`actions/` must stay import-light** — no chromadb / pyarrow / torch. Promotion into the vector store is a separate command (`pipeline.learn --promote`) run in its own process for exactly that reason.
11. **`outputs/event_log.parquet` is ONE global file that every profile overwrites.** Anything reading it must check whose data it holds. Ingestion stamps `outputs/event_log_profile.txt`; `bridge.export_actions` refuses to build a queue from a mismatched log, and the API re-ingests first. Without that guard the Today tab renders one SME's branding over another's findings — which is exactly what happened before the check existed.
12. **Set `DIAGNOSE_OFFLINE=1` in the API server's env for demos.** Otherwise a dashboard-triggered profile activation runs one Claude diagnosis per bottleneck (~30–90 s each) and bills for it. Also set `OLLAMA_MODEL=qwen2.5:1.5b` on the 8 GB laptop.
13. **Vite binds to IPv6.** `curl http://127.0.0.1:5173` fails; `http://localhost:5173` works. Only matters when smoke-testing from a shell.
14. **Never leave two uvicorn instances on port 8000.** Uvicorn sets `SO_REUSEADDR`, so a second instance binds happily and Windows hands each connection to *either* one — so half the requests get served by whichever build that instance is running. The symptom is maddening: the dashboard shows correct data, then stale data, with no pattern. Check with `netstat -ano | Select-String ":8000\s+.*LISTENING"` and expect exactly one PID. A `--reload` parent killed from a wrapper can leave its worker alive, so kill by command line: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn|multiprocessing-fork' } | Stop-Process -Force`.
15. **A profile switch re-ingests, and that takes minutes.** The API's subprocess timeout is 900s for ingest-shaped work (`_TIMEOUT_INGEST`) and 180s for everything else; a timeout now returns a 504 with the command in it rather than dropping the connection. The dashboard clears the previous SME's queue on switch and says it is analysing — it must never render a queue whose `profile` differs from the one requested.

## 9. What's next (candidates, none blocking)

- **Write-up** — system is feature-complete; the honest default is to pivot to the dissertation.
- **Deliberate 3rd foyle fork** — if a richer demo is wanted, add it *properly* in `generate_messy_foyle.py` + ground truth (not as leftover test files).
- **Bespoke Mapping / Bottlenecks tab layouts** — currently token-reskin only.
- **Post-freeze stretch** — Drive-listing wrapper for `audit/scan.py` (real SharePoint/Drive crawl); LLM-proposed freetext status value-maps.

Cut order if time is short: Drive wrapper → value-maps → model sweep. Non-cuttable
(carry thesis claims): both profiles, gate-#2 metrics, eval script.
