# TASKLIST.md — Build status & what to do next

Read `CLAUDE.md` first (the why). Read `HANDOVER.md` for run commands + gotchas.
This file tells an agent **exactly** what is done, what is next, and in what order.

---

## ✅ DONE — Do NOT rebuild these

> These are committed, tested, and working on `master`. Do not touch them unless
> explicitly asked to fix a bug or the current task depends on it.

### Pipeline (bottleneck-ingest)

- [x] **Synthetic data generation** — `synthetic/generate_messy_foyle.py` + `generate_messy_joinery.py`. Produces `data/synthetic/messy_<profile>/*.xlsx` + ground-truth JSONs. Seeded, reproducible, 5 files each.
- [x] **Canonical schema** — `Event` + `NormalisedRecord` in `models.py`. Fixed contract.
- [x] **PII scrub** — `scrub/anonymise.py`. spaCy NER + regex. Wholesale actor masking. Zero-PII test in test suite.
- [x] **Mapping-inference agent (`audit/`)** — Claude API (`claude-opus-4-8`), `messages.parse`, no temperature. `scan.py` (headers + scrubbed samples) → `infer.py` (LLM) → `propose.py` (offline fallback) → `run.py` (CLI). Writes `outputs/ui_mapping_proposal_<profile>.json`.
- [x] **Mapped reader** — `readers/mapped_reader.py`. Approved mapping + messy folder → canonical event rows. Dedup on (case_id, activity, timestamp) keep-first.
- [x] **Ingest (`--source messy --profile <p>`)** — hard errors if no approved mapping (enforces Gate 1). Writes `records.jsonl → ChromaDB → event_log.parquet`.
- [x] **Bottleneck detection** — `detection/detect_generic()`. Exactly 3 types: delay / repetition / rework. Parametrised by `MESSY_PROFILES[p]["markers"]`.
- [x] **Export** — `bridge/export_messy.py`. Writes `ui_cases.json` + `ui_workflow.json`. Nodes carry `sources` (which sheets feed each stage).
- [x] **Remediation executor (`remediate/`)** — status freetext → `{Complete, Open, N/A}`. Cleaned copies to `messy_<profile>_cleaned/` (originals untouched). CLI: `python -m remediate.run --profile <p> [--apply]`.
- [x] **Mapping eval** — `eval/score_mapping.py`. Baseline vs LLM vs human-approved F1. Results in `outputs/eval_mapping_<profile>.json`.
- [x] **Joinery profile (SME #2)** — identical pipeline, zero new code. Proved generalisability.
- [x] **Per-profile caching + active pointer** — `outputs/ui_cases_<p>.json`, `ui_workflow_<p>.json`, `active_profile.txt`. Instant re-switch.
- [x] **Resolution corpus (RAG knowledge base)** — `synthetic/generate_resolutions.py` → `data/synthetic/resolutions_<profile>.json`. 26 seeded, PII-free past resolutions per profile (6 per bottleneck type + 8 distractors so retrieval is non-trivial). Embedded into the **separate** `sme_resolutions` Chroma collection by `pipeline/embed_resolutions.py` (survives ingest resets; re-run only when the corpus JSON changes).
- [x] **RAG diagnosis agent** — `pipeline/diagnose.py`. Per bottleneck: top-3 resolutions from `sme_resolutions` → scrubbed evidence payload → `claude-opus-4-8` via `messages.parse` (adaptive thinking, no temperature) → `DiagnosisResult`. `offline_diagnosis()` is the deterministic fallback. Zero-PII + no-network tests in `tests/test_diagnose.py`.
- [x] **LangGraph agent** — `pipeline/agent.py`. `detect → retrieve → diagnose → gate → execute` StateGraph; Gate 2 is a conditional edge. Fresh run pauses `awaiting_gate` + writes `outputs/agent_run_<profile>_<ts>.json`; `--resume <run_id>` reads dashboard decisions and re-enters at the gate. Execute shells `remediate.run --apply` as its own process. Tests in `tests/test_agent.py`.
- [x] **Export via RAG diagnosis** — `bridge/export_messy.py` calls `diagnose()` per bottleneck (LLM supplies description / suggested_fix / confidence / retrieved_resolutions); authored `_TEMPLATES` remain the fallback on failure or offline. `ui_cases.json` keyset unchanged — dashboard untouched.
- [x] **Tests green** — 90+ tests pass (`pytest -q`).

### Dashboard (hitl-react)

- [x] **React UI** — Vite + Tailwind + @xyflow/react. `http://localhost:5173`.
- [x] **FastAPI backend** — `hitl-react/api/main.py`. Thin orchestrator, shells out to pipeline venv.
- [x] **Mapping Review tab (Gate 1)** — human edits roles/columns, approves, triggers re-ingest.
- [x] **Workflow DAG** — @xyflow/react. Hover stage → source sheets. Bottleneck stages flagged.
- [x] **Bottlenecks tab** — 3 bottleneck cards (delay/repetition/rework).
- [x] **Fixes tab (Gate 2)** — HITL approve/reject per bottleneck. RemediationPanel for data cleanup.
- [x] **Profile switcher** — click SME brand in header → dropdown → instant switch.
- [x] **Approval tracking** — `approvedMap` keyed by `proposal.generated_at` (fresh audit reopens gate).
- [x] **Stale remediation auto-refresh** — plan regenerates when event log / mapping newer.
- [x] **Mapping decision session log** — shows which sheets now feed workflow + skipped.

---

## ❌ NOT DONE — Tasks in priority order

Work through these **top to bottom**. Do not skip ahead. Mark [x] as you go.

### 🔴 Priority 1 — Dissertation integrity (must resolve before write-up)

- [x] **Reconcile LangGraph claim vs build — RESOLVED via Option B.**
  `pipeline/agent.py` now implements the stateful LangGraph graph
  (`detect → retrieve → diagnose → gate → execute`, langgraph 1.2.8 pinned).
  Run: `python -m pipeline.agent --profile foyle [--offline]`, then approve in the
  dashboard's Fixes tab and `python -m pipeline.agent --resume <run_id>`.
  Write-up note: the HITL gate is a *conditional edge* that terminates the run until a
  human decision artifact exists; resume re-enters the graph at the gate (two-phase run,
  no checkpointer — the run-state JSON is the auditable artifact).

- [ ] **Reconcile the Ollama claim (still open).**
  Ollama remains absent. Recommended: reframe the privacy story around the zero-PII scrub
  (implemented + tested for BOTH agents — `audit/` and `pipeline/diagnose.py`) rather than
  local inference, per the old Option A/C analysis.

- [ ] **Gate numbering — align code comments to doc.**
  CLAUDE.md + HANDOVER.md use chronological numbering: mapping = Gate 1, fixes = Gate 2.
  Code comments in `hitl-react/api/main.py` and some bridge files say the opposite.
  Do a search-and-replace pass when touching those files.

### 🟡 Priority 2 — Write-up artefacts (before report submission)

- [ ] **Phase 2 build report** — Detection + RAG diagnosis write-up. Template = `PHASE1_REPORT.md`.
  Cover: `detect_generic` design, 3 bottleneck types, precision/recall vs ground truth, RAG retrieval
  (ChromaDB `sme_resolutions`, MRR/NDCG — ground-truth relevance = profile AND bottleneck_type match),
  the diagnosis agent (scrubbed payload → `DiagnosisResult`), the LangGraph loop, and the
  template fallback story. The old limitation (retrieved_resolutions were corpus chunks, not
  curated resolutions) is now FIXED — cite the curated corpus instead.

- [ ] **Phase 3 build report** — Mapping-inference agent + HITL Gate 1.
  Cover: audit pipeline design, zero-PII guarantee (with test), offline vs LLM mode, F1 eval table
  (foyle .846→.968→1.0, joinery .308→.909→1.0), gate-ordering enforcement, decision logging.

- [ ] **Phase 4 build report** — Generalisation (second SME / joinery profile).
  Cover: what changes per SME (config block + approved mapping), what stays fixed (all of the pipeline),
  joinery eval numbers, the adapter-pattern demonstration.

- [ ] **Phase 5 build report** — Remediation executor + full HITL loop.
  Cover: data remediation design (freetext → controlled vocab), cleaned copies strategy, Gate 2 (fix
  approval), end-to-end flow both profiles, React dashboard tour.

- [ ] **Eval write-up / results section.**
  Cite `outputs/eval_mapping_foyle.json` + `outputs/eval_mapping_joinery.json`.
  Cover: F1 table, HITL correction counts + time-to-decision, detection P/R, RAG retrieval quality.
  Acknowledge limitations: synthetic data + injection/evaluation same author, timing data from dev runs.

- [ ] **Viva demo script update** — original script pre-dates mapping agent + joinery + React dashboard.
  Update to: (1) foyle full walkthrough (audit → mapping review → ingest → workflow → bottlenecks →
  fixes → remediation), (2) joinery compressed walkthrough stopping at canonical store, (3) live
  profile switch in the dashboard.

### 🟢 Priority 3 — Polish / nice-to-have (only if time allows before freeze)

- [ ] **Bespoke Mapping tab layout** — currently token-reskin only (same card grid). Could show
  the mess report more prominently, add a visual column-mapping flow.

- [ ] **Bespoke Bottlenecks tab layout** — currently simple cards. Could show affected-case timeline,
  severity heat-map.

- [ ] **Third foyle seasonal fork** — if the viva demo needs a richer "operator adds a messy sheet"
  walkthrough, add it *properly* in `generate_messy_foyle.py` + ground truth (NOT as leftover test files).
  This shifts the eval F1 numbers — re-run + re-cite.

- [ ] **Drive-listing wrapper for `audit/scan.py`** — real SharePoint/Drive crawl (post-freeze stretch;
  belongs to the live product boundary, not the dissertation deliverable).

---

## ⚠️ Constraints — check before any change

1. **No `temperature` on Opus 4.8** — 400 error. Applies to BOTH LLM callers: `audit/infer.py` and `pipeline/diagnose.py` (adaptive thinking instead).
2. **`audit/` and `remediate/` must NOT import chromadb / pyarrow / torch** — Windows segfault. They run as separate processes via `api/main.py → _run_pipeline()`. Relatedly, `pipeline/diagnose.py` and `pipeline/agent.py` must never import chromadb/torch at **module scope** — retrieval imports live inside functions/nodes (a fresh-interpreter test in `tests/test_agent.py` enforces it), and the agent's execute node shells `remediate.run` as its own process.
3. **fastparquet, not pyarrow** — pinned in `requirements.txt`. Do not swap.
4. **`PYTHONIOENCODING=utf-8`** — required for `✔` in status values (cp1252 default crashes).
5. **`.venv/Scripts/python.exe` explicitly** — bare `python` hits the Windows Store stub.
6. **Mapping drift** — browser approval overwrites `mappings/approved_<profile>.json`. If eval shifts: `git checkout mappings/approved_*.json`.
7. **`ANTHROPIC_API_KEY` in `.env`** — gitignored. **Rotate before submission** (key was pasted in a dev chat session).
8. **Bottleneck count is fixed at 3** — by design (3 detectors). More data = higher affected counts, not new types.
9. **Drive = 5 reproducible files** — do not add ad-hoc test files to `data/synthetic/messy_foyle/`. The ground-truth test asserts an exact file list.
10. **`sme_resolutions` is independent of ingest resets** — `reset_collection()` wipes only `sme_ops`. Re-run `python -m pipeline.embed_resolutions --profile <p>` only when `resolutions_<profile>.json` changes.
11. **Dashboard-triggered exports now attempt 3 Claude diagnosis calls** (~30–90 s per export). Set `DIAGNOSE_OFFLINE=1` in the hitl-react API server env (or pass `--offline`) to force the authored templates without code changes.
