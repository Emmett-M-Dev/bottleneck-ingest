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
- [x] **Tests green** — 25+ tests pass (`pytest -q`).

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

- [ ] **Reconcile LangGraph/Ollama claim vs build.**
  The original design stated LangGraph for orchestration and Ollama as the local dev LLM.
  **Neither is in the codebase.** The report cannot assert these while the repo has neither.
  Choose one path and implement it:
  - **Option A (recommended, zero build cost):** Update the write-up framing. Describe what's
    actually built: sequential Python pipeline + Claude API mapping agent + HITL gates. Reframe
    the privacy story around the zero-PII scrub (implemented + tested) rather than local inference.
  - **Option B:** Implement a LangGraph wrapper over the existing detect/diagnose/fix cycle if
    the "stateful agentic graph" claim is load-bearing for the contribution.
  - **Option C:** Wire Ollama as an alternative inference backend for the mapping agent (only if
    the "no sensitive data to external APIs" argument is a stated ethics commitment — the zero-PII
    scrub already mitigates this even with the Claude API).

- [ ] **Gate numbering — align code comments to doc.**
  CLAUDE.md + HANDOVER.md use chronological numbering: mapping = Gate 1, fixes = Gate 2.
  Code comments in `hitl-react/api/main.py` and some bridge files say the opposite.
  Do a search-and-replace pass when touching those files.

### 🟡 Priority 2 — Write-up artefacts (before report submission)

- [ ] **Phase 2 build report** — Detection + RAG diagnosis write-up. Template = `PHASE1_REPORT.md`.
  Cover: `detect_generic` design, 3 bottleneck types, precision/recall vs ground truth, RAG retrieval
  (ChromaDB, MRR/NDCG), honest limitation (retrieved_resolutions = corpus chunks, not curated resolutions).

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

1. **No `temperature` on Opus 4.8** — 400 error. Only in `audit/infer.py`.
2. **`audit/` and `remediate/` must NOT import chromadb / pyarrow / torch** — Windows segfault. They run as separate processes via `api/main.py → _run_pipeline()`.
3. **fastparquet, not pyarrow** — pinned in `requirements.txt`. Do not swap.
4. **`PYTHONIOENCODING=utf-8`** — required for `✔` in status values (cp1252 default crashes).
5. **`.venv/Scripts/python.exe` explicitly** — bare `python` hits the Windows Store stub.
6. **Mapping drift** — browser approval overwrites `mappings/approved_<profile>.json`. If eval shifts: `git checkout mappings/approved_*.json`.
7. **`ANTHROPIC_API_KEY` in `.env`** — gitignored. **Rotate before submission** (key was pasted in a dev chat session).
8. **Bottleneck count is fixed at 3** — by design (3 detectors). More data = higher affected counts, not new types.
9. **Drive = 5 reproducible files** — do not add ad-hoc test files to `data/synthetic/messy_foyle/`. The ground-truth test asserts an exact file list.
