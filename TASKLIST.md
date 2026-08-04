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
- [x] **Bottleneck detection — DYNAMIC** — `detection/dynamic.py::detect_dynamic()`. Statistical scan of EVERY stage, no marker config: delay = entry gaps beyond the log's own Q3+1.5×IQR threshold, repetition = a stage re-entered with no later stage in between, rework = a genuine backward transition vs `stage_order`. Returns 0..N bottlenecks ordered by impact. `detect_generic()` + `MESSY_PROFILES[p]["markers"]` remain **eval-only** (the baseline the dynamic detector is scored against).
- [x] **LLM anomaly pass** — `detection/anomaly.py`. Aggregate per-stage stats (stage names + numbers ONLY — a leak test enforces it) → local Ollama model proposes up to 3 advisory findings → `type="anomaly"` cards badged "AI-spotted — unverified". Skipped silently when Ollama is absent.
- [x] **Local-LLM provider layer** — `pipeline/llm.py`. Ollama `/api/chat` JSON mode, schema-in-prompt, pydantic validation + 1 retry, None on any failure. Claude keeps the precision tasks (mapping, diagnosis); Ollama takes the exploratory pass — resolves the §6a Ollama claim.
- [x] **Detection eval** — `eval/score_detection.py`. Marker baseline vs dynamic, P/R/F1 per type vs seeded ground truth. Results (after foyle/joinery were reseeded with parked operational cases, P0 item 4 below): baseline macro-F1 0.486 (foyle, was 0.524) / 0.487 (joinery, was 0.523) / 0.471 (advisory, untouched) — presence-based markers collapse further on structural patterns once parked cases pass through the same marker-named stages — vs dynamic **1.000 / 1.000 / 1.000** (unchanged; recall stayed 1.0 for every type throughout, only baseline precision moved).
- [x] **Cost model** — `MESSY_PROFILES[p]["costs"]` → per-case `estimated_cost` with the basis spelled out ("5 cases × 15 days × £35/day"); total in the workflow KPIs.
- [x] **Learning loop** — `pipeline/learn.py`. Approved/modified Gate-2 fixes append to `data/learned/learned_resolutions_<p>.json` (RES-LRN-…, source="learned") and upsert into `sme_resolutions` — the next diagnosis retrieves the SME's own approved fixes. Fired by the API on POST /api/decisions (own process). Idempotent on decision_id.
- [x] **Impact history** — every export appends a snapshot to `outputs/history_<p>.jsonl`; a remediation apply appends one too (messy_cells → 0). Served by GET /api/history/{p}; Dashboard ImpactPanel sparklines + a badged PROJECTION line.
- [x] **Zero-PII payload viewer** — every case carries `llm_payload` (the exact scrubbed diagnosis payload, built even offline); the dashboard's "what the AI saw" modal highlights the anonymisation placeholders.
- [x] **Export** — `bridge/export_messy.py`. Writes `ui_cases.json` + `ui_workflow.json`. Nodes carry `sources` (which sheets feed each stage).
- [x] **Remediation executor (`remediate/`)** — status freetext → `{Complete, Open, N/A}`. Cleaned copies to `messy_<profile>_cleaned/` (originals untouched). CLI: `python -m remediate.run --profile <p> [--apply]`.
- [x] **Mapping eval** — `eval/score_mapping.py`. Baseline vs LLM vs human-approved F1. Results in `outputs/eval_mapping_<profile>.json`.
- [x] **Joinery profile (SME #2)** — identical pipeline, zero new code. Proved generalisability.
- [x] **Per-profile caching + active pointer** — `outputs/ui_cases_<p>.json`, `ui_workflow_<p>.json`, `active_profile.txt`. Instant re-switch.
- [x] **Resolution corpus (RAG knowledge base)** — `synthetic/generate_resolutions.py` → `data/synthetic/resolutions_<profile>.json`. 26 seeded, PII-free past resolutions per profile (6 per bottleneck type + 8 distractors so retrieval is non-trivial). Embedded into the **separate** `sme_resolutions` Chroma collection by `pipeline/embed_resolutions.py` (survives ingest resets; re-run only when the corpus JSON changes).
- [x] **RAG diagnosis agent** — `pipeline/diagnose.py`. Per bottleneck: top-3 resolutions from `sme_resolutions` → scrubbed evidence payload → `claude-opus-4-8` via `messages.parse` (adaptive thinking, no temperature) → `DiagnosisResult`. `offline_diagnosis()` is the deterministic fallback. Zero-PII + no-network tests in `tests/test_diagnose.py`.
- [x] **LangGraph agent** — `pipeline/agent.py`. `detect → retrieve → diagnose → gate → execute` StateGraph; Gate 2 is a conditional edge. Fresh run pauses `awaiting_gate` + writes `outputs/agent_run_<profile>_<ts>.json`; `--resume <run_id>` reads dashboard decisions and re-enters at the gate. Execute shells `remediate.run --apply` as its own process. Tests in `tests/test_agent.py`.
- [x] **Export via RAG diagnosis** — `bridge/export_messy.py` calls `diagnose()` per bottleneck (LLM supplies description / suggested_fix / confidence / retrieved_resolutions); authored `_TEMPLATES` remain the fallback on failure or offline. `ui_cases.json` keyset unchanged — dashboard untouched.
- [x] **Tests green** — 190 tests pass (`pytest -q`).

### Action layer (2026-07-23) — the worker-facing product

- [x] **Generic action/intervention models** — `actions/models.py`: ActionItem,
  Intervention, InterventionOutcome, BusinessImpact, EvidenceReference,
  AnalysisSnapshot. Nothing SME-specific; labels, stage order, monetary
  assumptions and templates all come from `config.MESSY_PROFILES[<p>]`.
- [x] **Lifecycle state machine** — `actions/lifecycle.py`.
  `proposed → approved → assigned → in_progress → completed → outcome_review →
  validated | ineffective`, with `rejected`/`dismissed` off-ramps. Illegal
  transitions raise. Full history kept on every intervention, including the
  ones that failed.
- [x] **Case-level detection** — `detection/case_rules.py`: six generic rules
  producing findings about individual cases rather than stages.
- [x] **Findings → evidence-backed items** — `actions/build.py`. Two separate
  confidences (detection vs data quality), evidence carrying spreadsheet row
  references, per-case detail.
- [x] **Deterministic explainable ranking** — `actions/rank.py`. Every point
  scored comes with a sentence saying where it came from.
- [x] **Execution routing corrected** — `actions/execute.py` + the rewritten
  `pipeline.agent.execute_node`. Only `data_quality` items whose template is on
  `MACHINE_EXECUTABLE_TEMPLATES` reach the remediation executor. Approving an
  operational fix no longer runs status normalisation.
- [x] **Outcome-gated learning** — `pipeline/learn.py` split into a pending
  (audit-trail) store and a trusted store. Only validated-effective
  interventions are embedded. `--migrate-legacy` demoted the 3 foyle entries
  written under the old rule.
- [x] **Outcome measurement** — `actions/outcome.py`. Baseline (measured),
  expected (projected), observed (measured) kept as separate fields;
  tri-state `effective` with a 10% noise band; human validation required.
- [x] **Third SME profile** — `advisory` / Northstar Advisory. Config block,
  `synthetic/generate_messy_advisory.py` (+ `--follow-up` for the later
  snapshot), ground truth, approved mapping, RAG corpus. Zero new core code.
- [x] **`ingest.py --drive`** — re-analyse a later snapshot of the same drive
  through the same approved mapping.
- [x] **Action queue export + API** — `bridge/export_actions.py`, `actions/cli.py`,
  six new FastAPI endpoints.
- [x] **React "Today" view** — `src/components/today/`. Primary tab; workflow
  map and bottleneck cards demoted to supporting evidence.
- [x] **UTF-8 subprocess decoding bug fixed** — `text=True` was decoding child
  stdout as cp1252 and dying on `✔`.

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

### 🔴 Priority 0 — Follow-ups created by the action-layer work (2026-07-23)

- [x] **Re-ran + re-cited the longitudinal replay.** `eval/replay.py` is now
  outcome-gated: the oracle approves, completes, and only a *measured*
  improvement at a later tick is validated and embedded. Ran for both foyle and
  joinery and regenerated the figures. Result: **`lifecycle.validated` stayed at
  0 for the full 9-tick window, in both profiles** — no oracle-approved fix was
  ever completed and re-measured against a later tick showing genuine
  improvement, so nothing entered `sme_resolutions`. `lifecycle.approved_unmeasured`
  (what the old approval-gated loop would have trusted by the same tick) reached
  **3** in both profiles (foyle by tick 4, joinery by tick 6) and held. Cause,
  traced to source: affected-case counts only *grow* in a recording (2→3→4), so
  `actions/outcome.py::compare` can never return a measured improvement inside
  this window; `tests/test_replay.py` proves the validation path does work when
  a finding genuinely disappears, so this is the honest behaviour of a sound
  mechanism, not a bug. The previously cited "learned-hit rate 0 → 1" does
  **not** survive outcome gating — see `CLAUDE.md` §7. `outputs/replay_pending_<p>.json`
  and `outputs/replay_interventions_<p>.json` substantiate both curves;
  `replay_learned_<p>.json` was not produced (only written on a promotion, and
  none occurred).

- [x] **Filled the LLM column for `advisory`.** Ran `python -m audit.run
  --profile advisory` online, replacing the offline heuristic-only proposal.
  Result: LLM column F1 = 0.766 (baseline 0.500, human-approved 1.000) — see
  `outputs/eval_mapping_advisory.json`.

- [x] **Restored `mappings/approved_foyle.json`.** `git checkout
  mappings/approved_foyle.json` would NOT have fixed the drift — the drifted
  mapping had itself been committed (`c92caef`). Recovered the pre-drift
  mapping from its known-good commit instead
  (`git checkout a8e3437 -- mappings/approved_foyle.json`, committed as
  `e2eb650`). Result: column F1 back to 1.000. Note `role_accuracy` is 0.6 —
  that predates the drift and is a separate, genuine finding (CLAUDE.md §7),
  not something this fix touches.

- [x] **Seeded operational patterns into foyle/joinery.** Both drives now carry
  *parked* cases (stalled at a stage, unowned, piled onto one owner) alongside
  their original structural delay/repetition/rework patterns, mirroring the
  advisory drive's design: each generator records only *where* a case was
  parked; `detection/case_rules.py` decides independently whether that breaches
  an SLA (same circularity guard as advisory), and flags strictly fewer cases
  than were parked (tests assert it, both profiles). Neither drive has a money
  column, so `unrealised_value` stays out of reach for foyle/joinery **by
  design** — a test pins that absence. This did move the eval numbers as
  expected (detection macro-F1 baseline: foyle 0.524→0.486, joinery 0.523→0.487,
  dynamic unchanged at 1.000/1.000 — see the DONE section's detection-eval line
  above), which is the deliberate, accepted trade-off. Result: foyle and
  joinery action queues are no longer thin — 12 items each (`stage_sla_breach`
  ×4, `stalled_case` ×1, `unowned_case` ×1, `overloaded_owner` ×1, plus the
  original `delay`/`rework`/`repetition`/`messy_status_values`/`stale_duplicate_file`
  ×1 each).

### 🔴 Priority 1 — Dissertation integrity (must resolve before write-up)

- [x] **Reconcile LangGraph claim vs build — RESOLVED via Option B.**
  `pipeline/agent.py` now implements the stateful LangGraph graph
  (`detect → retrieve → diagnose → gate → execute`, langgraph 1.2.8 pinned).
  Run: `python -m pipeline.agent --profile foyle [--offline]`, then approve in the
  dashboard's Fixes tab and `python -m pipeline.agent --resume <run_id>`.
  Write-up note: the HITL gate is a *conditional edge* that terminates the run until a
  human decision artifact exists; resume re-enters the graph at the gate (two-phase run,
  no checkpointer — the run-state JSON is the auditable artifact).

- [x] **Reconcile the Ollama claim — RESOLVED (hybrid local/cloud).**
  `pipeline/llm.py` + `detection/anomaly.py`: the exploratory anomaly pass runs on a
  local Ollama model (`qwen2.5:7b` default, `OLLAMA_MODEL`/`OLLAMA_URL` env to change);
  Claude keeps the two precision tasks (mapping inference, RAG diagnosis). Write-up
  framing: local inference for zero-cost exploratory analysis + the zero-PII scrub for
  the cloud calls — both privacy controls implemented and tested.

- [ ] **Gate numbering — align code comments to doc.**
  CLAUDE.md + HANDOVER.md use chronological numbering: mapping = Gate 1, fixes = Gate 2.
  Code comments in `hitl-react/api/main.py` and some bridge files say the opposite.
  Do a search-and-replace pass when touching those files.

- [x] **FIXED — structural diagnosis join used to key on rank order, not content.**
  `actions/build.py::_structural_items` joined diagnosis prose onto detected
  bottlenecks by `bn.id`, and `detection/dynamic.py` assigns `id` purely by
  rank order (`f"BN{i:03d}"`). It had only ever lined up by luck — confirmed
  during the 2026-08-01 action-queue rebuild, when a stale `ui_cases_<profile>.json`
  cache (predating the foyle/joinery reseed) happened to still match because
  the reseed didn't reorder the three structural types. A future reseed that
  *did* reorder delay/repetition/rework would have silently mis-attributed one
  bottleneck's diagnosis text to another, with no error raised. Fixed in
  `1167a6e` ("Key diagnosis prose to findings by content, not rank order"):
  `detection/detect.py::finding_key(bn)` returns a content hash of
  `(type, stage, metric_label)`, and the join in `actions/build.py` plus both
  exporters now key on it instead of `bn.id`. See HANDOVER.md §8 gotcha 16.

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
6. **Mapping drift** — browser approval overwrites `mappings/approved_<profile>.json`. If eval shifts, do NOT `git checkout mappings/approved_*.json` — a drifted mapping can itself be committed (foyle's was, in `c92caef`), so that command restores the drift rather than fixing it. Recover from the last known-good commit instead: `git checkout <good-commit> -- mappings/approved_<profile>.json` (foyle's known-good mapping is in `a8e3437`).
7. **`ANTHROPIC_API_KEY` in `.env`** — gitignored. **Rotate before submission** (key was pasted in a dev chat session).
8. **Bottleneck count is DYNAMIC** — `detect_dynamic()` returns 0..N findings; the seeded drives currently yield 3 per profile plus optional anomaly cards. `markers` in config are eval-only (baseline detector) — do not wire them back into the pipeline. The seeded patterns are STRUCTURAL (duplicate stage entries, backward transitions); regenerating drives with marker-named stages would break `eval/score_detection.py`'s story.
9. **Drive = 5 reproducible files** — do not add ad-hoc test files to `data/synthetic/messy_foyle/`. The ground-truth test asserts an exact file list.
10. **`sme_resolutions` is independent of ingest resets** — `reset_collection()` wipes only `sme_ops`. Re-run `python -m pipeline.embed_resolutions --profile <p>` only when `resolutions_<profile>.json` changes.
11. **Dashboard-triggered exports attempt one Claude diagnosis call per bottleneck** (~30–90 s per export) plus one local Ollama anomaly call. Set `DIAGNOSE_OFFLINE=1` in the hitl-react API server env (or pass `--offline`) to force templates + skip the anomaly pass without code changes.
12. **Ollama is optional** — no local model = anomaly pass silently absent, nothing breaks. For the demo: install Ollama for Windows + `ollama pull qwen2.5:7b`. **RAM:** 7b needs ~6 GB free to load; on Emmett's 8 GB laptop it fails (`unable to allocate CPU_REPACK buffer`). This machine runs `qwen2.5:1.5b` instead — set `OLLAMA_MODEL=qwen2.5:1.5b` in the hitl-react API server env (and any CLI shell running a live export). Code default stays 7b (the dissertation claim); the env knob is the per-machine override. Verified working end-to-end on 1.5b (produces 3 anomaly findings on foyle).
13. **`sme_resolutions` now also holds learned entries** (`RES-LRN-…`, source="learned", from `data/learned/`). `pipeline.embed_resolutions --reset` wipes them from the collection — re-run `python -m pipeline.learn`'s embed (or re-approve) after a reset, or just re-run `python -c "from pipeline.learn import embed_learned; embed_learned('<p>')"`.
14. **FIXED — `actions/build.py::_structural_items` used to join diagnosis prose by `bn.id`, a rank-order id, not a content key.** A future reseed that reordered the structural pattern types would have silently mis-attributed diagnosis text between bottlenecks — not a hypothetical: this is exactly what a stale `ui_cases_<profile>.json` cache was found doing (correctly, by luck) after the 2026-08-01 foyle/joinery reseed. Fixed in `1167a6e`: the join now keys on `detection/detect.py::finding_key(bn)`, a content hash of `(type, stage, metric_label)`. See TASKLIST Priority 1 and HANDOVER.md §8 gotcha 16.
15. **`DIAGNOSE_OFFLINE` does not protect every export** — only `bridge/export_messy.py` reads it. `bridge/export_actions.py` has no live-diagnosis path at all; its structural items' diagnosis text comes entirely from whatever `outputs/ui_cases_<profile>.json` cache is already on disk. Setting the env var before running `export_actions` alone does nothing — refresh the cache with `export_messy --offline` (or `DIAGNOSE_OFFLINE=1`) first if template-only text is required.
