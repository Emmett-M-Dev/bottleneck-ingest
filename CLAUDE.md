# CLAUDE.md

Context file for AI coding assistants working on this project. Read this first, every session.

> **Companion docs:** `HANDOVER.md` (current build state + how to run) and
> `TASKLIST.md` (what's done, what's next). This file is the *why*; HANDOVER.md is the *what/how now*.

---

## 1. Project Snapshot

- **Title:** Agentic Workflow Optimisation for SME Operations — An End-to-End Pipeline for Bottleneck Detection, RAG-Based Diagnosis and Human-in-the-Loop Approval
- **Module:** COM748 MSc Research Project (Ulster University, MSc Artificial Intelligence)
- **Student:** Emmett Murray (B00810618)
- **Supervisor:** Dr Jose Santos
- **Case study / validation partner:** Foyle — an educational-tourism SME

**One line:** A lightweight, locally-run agentic pipeline that ingests messy SME data, detects operational bottlenecks, diagnoses them with RAG-grounded suggestions, and executes approved fixes — with a human approving every consequential action.

**Product promise (the worker-facing framing):** an operational action queue for
spreadsheet-run SMEs that identifies what needs attention today, explains the
evidence behind it, helps staff take the action, and measures whether the action
worked. The worker loop is:

```
evidence → constraint → affected cases → recommended action → owner
        → due date → completion → measured outcome
```

Charts and aggregate bottleneck summaries are **supporting evidence** for that
queue, not the product.



---

## 2. THE RULE THAT MATTERS MOST — Scope Boundary

There are **two separate things**, and they must never be conflated in code:

1. **The dissertation deliverable (THIS repo).** A *local, file-based system running on Emmett's laptop.* Synthetic data. No live credentials. No production deployment. This is what gets built, evaluated, and submitted.
2. **A live deployed product for Foyle.** A *post-submission conversation.* Live connectors, real credential handling, and any real-time crawling belong on **that** side of the line — i.e. NOT in the dissertation build.

**When in doubt, build for (1).** If a task smells like "connect to Foyle's real SharePoint / handle real OAuth against live data / deploy a service," stop and flag it — it's almost certainly out of dissertation scope.

Real Foyle data is only ever used as a **one-off, consented, supervisor-signed-off export** — never live credential-based extraction.

**Scope model, as-built (evolved from the original design):** the Foyle SharePoint
audit killed the earlier synthetic 6-sheet / `foyle-tracker` model. The pipeline now
ingests **messy per-SME synthetic drives** — a folder of deliberately messy
spreadsheets per profile: `data/synthetic/messy_<profile>/*.xlsx`, ingested via
`ingest.py --source messy --profile <p>`. The older sources (`foyle`,
`foyle-tracker`, `foyle-tracker-sheets`, `foyle-sheets`, `sheets`, `all`) were
**removed on 2026-08-05** — see §11. `messy` and `local` are the only two left.

`ingest.py --drive <path>` re-analyses a **later snapshot of the same drive**
through the same approved mapping (no second trip through Gate 1). That is what
makes outcome measurement possible end-to-end:
`data/synthetic/messy_advisory_followup/` is the advisory drive a fortnight on,
after the action queue was worked.

---

## 3. Academic Framing (why the design choices exist)

The contribution is **democratising intelligent process automation for SMEs**, in deliberate contrast to AI consolidating around large enterprises. OECD data: ~40% of large firms use AI vs ~12% of small firms — the gap is driven by resource constraints (ROI uncertainty, no AI-ready data, skills gaps), not technical sophistication. So the system must be **lightweight and runnable without a specialist team**.

**The generalisability claim now rests on demonstrated evidence, not assertion.**
Three contrasting SMEs run through the **identical** pipeline core:

| Profile id | Fictional SME | Workflow |
|---|---|---|
| `foyle` | Foyle International | educational-tourism placement |
| `joinery` | McCrossan Joinery | trades / fit-out job pipeline |
| `advisory` | **Northstar Advisory** | professional services, lead-to-cash |

Onboarding SME #2 and SME #3 each added a **config block + synthetic drive +
approved mapping** and **zero new reader, detector or action code**. That is the
thesis payoff (see §4, §7). Any code that hard-codes SME-specific logic into the
pipeline core undermines it.

`advisory` is the commercially recognisable demo (money, capacity and delivery
risk are explicit in the data); `foyle` and `joinery` remain the contrasting
evidence that the same core works on very different workflows.

---

## 4. Architecture — the three-layer model

Think of it like an **electrical plug adapter**: the appliance (the pipeline core) never changes; only the thin adapter that fits the local socket changes per country. Or in Python terms: the pipeline core codes against an **abstract base class**; each SME provides a concrete implementation.

```
   MESSY SME FILES (messy_<profile>/*.xlsx — deliberately messy, per SME)
              │
   ┌──────────▼───────────┐
   │  ADAPTER LAYER        │  ← THIN, changes per SME.
   │  (per-SME)            │     Mapping-inference agent (Claude) PROPOSES mappings here.
   └──────────┬───────────┘
              │  [HITL GATE 1 — Mapping Review: human confirms proposed schema mappings]
   ┌──────────▼───────────┐
   │  CANONICAL SCHEMA     │  ← THE MIDDLE. Identical across all SMEs.
   │  + canonical store    │     Event(case_id, activity, timestamp, actor, status, source_ref).
   └──────────┬───────────┘
              │
   ┌──────────▼───────────┐
   │  FIXED PIPELINE CORE  │  ← IDENTICAL across SMEs. The academic constant.
   │  1. Bottleneck detection (detect_dynamic: statistical scan of EVERY
   │     stage, 0..N findings — delay / repetition / rework, no marker config)
   │  2. RAG diagnosis (over the resolution store, incl. LEARNED fixes)
   │  3. Fix suggestion (+ per-profile cost model)
   └──────────┬───────────┘
              │  [HITL GATE 2 — Fixes: human approves / rejects / modifies each fix]
   ┌──────────▼───────────┐
   │  ACTION LAYER         │  ← GENERIC (actions/). Findings + case rules become
   │  (actions/)           │     ActionItems: evidence, affected cases, impact,
   │                       │     owner, due date, lifecycle. Ranked deterministically.
   └──────────┬───────────┘
              │  [HITL GATE 2 — approve / reject / dismiss, with owner + due date]
   ┌──────────▼───────────┐
   │  ROUTING BY CATEGORY  │  ← The ONLY door to automated execution.
   │  (actions/execute.py) │
   └────┬─────────────┬────┘
        │             │
   data_quality    case_action / process_intervention
   + machine-safe        │
        │                ▼
        ▼          TRACKED INTERVENTION (nothing is executed)
   REMEDIATION EXECUTOR        │
   (cleaned copies)            ▼
        │            completed → measured against a LATER analysis
        └──────────────┬───────┘
                       ▼
              validated | ineffective   ← only `validated` + effective
                       │                  becomes trusted RAG knowledge
                       ▼
              LEARNING LOOP (pipeline/learn.py)
```

**Two HITL gates, not one** — numbered in chronological order:
- **Gate 1 (ingestion / Mapping Review):** the mapping-inference agent examines messy
  files and *proposes* schema mappings; a human confirms/edits before anything is
  trusted. Corrections + time-to-decision are logged (`mapping_decisions.jsonl`) —
  these are the measurable HITL numbers.
- **Gate 2 (execution / Fixes):** the classic approval gate — no action executes
  without explicit human approve/reject/modify.

> ⚠️ **Numbering note:** some in-code comments historically labelled the *fix* gate
> "#1" and the *mapping* gate "#2". The canonical numbering is the chronological one
> above (mapping = Gate 1). Align comments to this when touched.

**The execution agent, as-built = the Remediation Executor** (`remediate/`). It performs
**data remediation**: messy freetext status values → controlled vocab `{Complete, Open,
N/A}`, writing **cleaned copies** to `messy_<profile>_cleaned/` (originals never touched)
plus a before→after diff. Note the distinction: the *bottleneck fixes* themselves are
**process advice for staff** (SLAs, checklists); the executor cleans the *data*.

**Two distinct stores — do not merge them:**
- **Canonical data store** — the operational data, normalised (`event_log.parquet` — the "what is happening" data).
- **RAG knowledge / resolution store** — the searchable index of past resolutions (the "how similar problems were fixed" knowledge). This is the ChromaDB vector store the diagnosis layer retrieves against.

### 4a. The three kinds of recommendation — never conflate them

This distinction is load-bearing: it is what stops an approved "chase the
overdue invoices" from rewriting spreadsheet status columns.

| Kind | Examples | What approving does |
|---|---|---|
| **`data_quality`** | normalise inconsistent statuses, flag duplicate/stale copies, repair a mapping | *Some* are machine-executable: only templates on `actions.models.MACHINE_EXECUTABLE_TEMPLATES` (currently `normalise_status_values`) reach the remediation executor, writing **cleaned copies**. The rest are tracked human work. |
| **`case_action`** | follow up an overdue lead, chase a client approval, assign an unowned job, raise an invoice | **Never** machine-executed. Becomes a tracked ActionItem + Intervention with an owner and a due date. |
| **`process_intervention`** | change an approval rule, rebalance capacity, cross-train, add a WIP limit, weekly invoicing checkpoint | Becomes a **measurable experiment**: baseline metric, expected improvement (a projection), owner, review date, success metric. |

Routing lives in `actions/execute.py::route`; the category comes from
`actions/templates.py`. `ActionItem.is_machine_executable` is the single
predicate and nothing else may authorise a file write.

### 4b. Approval is not proof — the intervention lifecycle

```
proposed → approved → assigned → in_progress → completed
         → outcome_review → validated | ineffective
         (rejected / dismissed are terminal off-ramps, kept in the audit trail)
```

Only `Intervention.is_trusted_knowledge` — status `validated`, an outcome
measured against a **later** analysis showing real improvement, AND a human
confirming that reading — promotes a fix into the retrievable resolution store.

Two learned stores, and the gap between them is the point:
- `data/learned/pending_resolutions_<p>.json` — every approval, forever. The
  audit trail. **Never embedded, never retrievable as advice.**
- `data/learned/learned_resolutions_<p>.json` — validated-effective only.
  Embedded into `sme_resolutions`.

`python -m pipeline.learn --profile <p> --migrate-legacy` demotes entries
written under the old approval-is-proof rule (already run for foyle: 3 demoted).

**Projections vs observations are different fields everywhere they meet** —
`BusinessImpact.is_projection`, `Intervention.expected_improvement_pct` vs
`InterventionOutcome.observed_value`. The UI labels them differently and the
effectiveness verdict never reads a projection. `effective` is tri-state:
`None` means "not enough evidence yet", which is the honest answer most often.

---

## 5. What's IN scope vs OUT (the "audit" trap)

The word "audit" previously conflated three things. They are now separated:

| Thing | Status |
|---|---|
| Real SharePoint crawler | ❌ **OUT of scope** (belongs to the live product) |
| Hand-written adapter code per SME profile | ✅ **Mandatory** |
| Mapping-inference agent (`audit/`, examines messy files, proposes mappings for human confirmation) | ✅ **In scope, built.** Adds HITL Gate 1, strengthens generalisability, produces evaluation data. |

---

## 6. Tech Stack (as-built)

| Layer | Tool | Notes |
|---|---|---|
| Action layer | **pure Python + pydantic** (`actions/`) | Models, lifecycle, ranking, JSON store, category routing. Deliberately free of chroma/pyarrow/torch so it can run inside the light processes. |
| Pipeline orchestration | **LangGraph** (`pipeline/agent.py`) + sequential CLI modules | `detect → retrieve → diagnose → gate → execute` StateGraph; the CLI modules (`ingest.py` etc.) remain the per-step entry points. |
| Mapping-inference agent | **Claude API — `claude-opus-4-8`** | `audit/infer.py` only. `messages.parse` with a Pydantic schema. **No `temperature`** (400s on Opus 4.8). Zero raw PII in the payload (scrubbed first). |
| RAG diagnosis agent | **Claude API — `claude-opus-4-8`** | `pipeline/diagnose.py`. Same call pattern; scrubbed payload; template fallback. |
| Vector store | **ChromaDB** | Powers the RAG resolution store. |
| Embeddings | **sentence-transformers** | |
| HITL UI (current) | **React** — `../hitl-react`, a **SEPARATE repo and a sibling of this one**, NOT a subdirectory | Vite + Tailwind + `@xyflow/react`. FastAPI backend in `../hitl-react/api/` is a thin orchestrator that shells out to *this* repo's venv. It resolves the pipeline as `_HERE.parent.parent / "bottleneck-ingest"`, so the sibling relationship is load-bearing. Looking for it inside this repo finds nothing. |
| HITL UI (legacy) | **Streamlit** — `../hitl-interface`, also a sibling repo | Superseded by hitl-react; `hitl-react/api/main.py` still points at its `fixtures/cases.json` as a fallback. |
| File parsing | **openpyxl**, **pandas** | Excel + tabular. `engine="openpyxl"`. |
| Parquet | **fastparquet** (NOT pyarrow) | Pinned. See constraint below. |
| Email execution | **smtplib** | |
| Env | Python + venv (per repo) | Version-pinned. `bottleneck-ingest/.venv`; `../hitl-react/api/.venv` (py3.14). |
| Dev in | Jupyter + VS Code | Task-by-task, supervised. |

**Parquet:** use **`fastparquet`**, NOT `pyarrow`. Importing pyarrow eagerly loads the
Arrow C++ runtime, which **segfaults in-process** with chroma/hnswlib + torch on Windows.
Pinned intentionally — do not "helpfully" swap it. Relatedly, `audit/` and `remediate/`
run as **separate processes** and must **never import chromadb / pyarrow / torch**.

### 6a. LangGraph ✅ shipped · Ollama ❌ trialled and withdrawn

One of the two original design claims shipped. The other was trialled and
withdrawn, and that is a finding rather than a gap.

- **LangGraph — shipped.** `pipeline/agent.py`: `detect → retrieve → diagnose →
  gate → execute` StateGraph (langgraph 1.2.8 pinned). Gate 2 is a conditional
  edge; a fresh run pauses `awaiting_gate` and writes
  `outputs/agent_run_<profile>_<ts>.json`; `--resume` reads the dashboard's
  decisions and re-enters at the gate (two-phase run, no checkpointer).

- **Ollama — trialled in development, then removed.** A local `qwen2.5:7b`
  drove an exploratory anomaly pass during development. Running it locally
  proved too compute-heavy on the development machine, so it was uninstalled
  and the work moved to the Claude API. The pass itself was removed on
  2026-08-05 (`pipeline/llm.py`, `detection/anomaly.py`, and the "AI-spotted"
  cards) — with no engine it could never fire, and the stored queues confirmed
  it: zero `llm_anomaly` items across all three profiles.

**Write it up as a finding, in the past tense.** §3 argues the SME AI adoption
gap is driven by resource constraints rather than technical sophistication.
First-hand evidence that local inference on a single commodity machine was
materially taxing supports that argument, and it is an observation rather than
an assertion — which is more than the original hybrid claim offered.

Two constraints on how it is written:
- It is **not** a privacy control of the delivered system. §9 has one
  implemented control, the zero-PII scrub, not two.
- [YOU] For this to be a finding rather than an anecdote it needs a number
  beside it — machine spec, the model, and rough observed latency or memory
  pressure, labelled as approximate. Without one a viva panel will say so.

---

## 7. Data Strategy — synthetic-first, ground-truth-by-design

- Datasets are **synthetic**, one messy drive per SME profile (`messy_foyle`,
  `messy_joinery`, `messy_advisory`, plus `messy_advisory_followup` — the same
  advisory drive a fortnight later, which is what outcomes are measured against).
- **3 bottleneck pattern types** (delay / repetition / rework), **injected by design** as
  STRUCTURAL patterns (outlier gaps, literal duplicate stage entries, genuine backward
  transitions), so ground truth is known before detection runs. Detection is **dynamic**
  (`detection/dynamic.py`) — 0..N findings per run, no marker config; the count is a
  property of the data.
- **Circularity guard:** generating *and* evaluating on data designed by the same person is a validity risk. Injection logic (`synthetic/generate_messy_*.py`) and detection logic (`detection/dynamic.py`) are cleanly separated; the detector does not know the injection rules.
- **Detection metrics (`eval/score_detection.py`):** P/R/F1 per pattern type, marker
  baseline vs dynamic detector, against the seeded ground truth:

  | Profile | baseline macro-F1 | dynamic macro-F1 |
  |---|---|---|
  | foyle | 0.486 | 1.000 |
  | joinery | 0.487 | 1.000 |
  | advisory | 0.471 | 1.000 |

  The presence-based marker baseline collapses on structural repetition/rework — the
  argument for statistical detection, mirroring the mapping eval's baseline→LLM gap.
  foyle and joinery moved from their earlier 0.524/0.523 once parked operational
  cases were seeded alongside the structural patterns (below): recall held at 1.0
  for every type on both profiles, throughout, but repetition/rework precision fell
  further (foyle repetition F1 0.286→0.25, rework 0.286→0.207) because the baseline
  flags any case that merely *passes through* a marker-named stage — the new parked
  cases do exactly that without exhibiting the pattern. The gap between baseline and
  dynamic therefore got wider, not narrower: a stronger result for statistical
  detection, not a regression. advisory's drive was untouched by this plan, so its
  figures stand unchanged.
- **Case-level rules (`detection/case_rules.py`)** sit alongside the structural
  detector and answer the worker's question rather than the analyst's: which
  individual cases need attention. Six generic rules — `stage_sla_breach`,
  `stalled_case`, `unowned_case`, `unrealised_value`, `overloaded_owner`,
  `key_person_dependency` — all driven by `MESSY_PROFILES[<p>]["case_rules"]`,
  no SME vocabulary in the rule code. `as_of` defaults to the newest event in
  the log, so runs stay reproducible on synthetic data.
  Circularity guard again: the advisory generator records only *where it parked
  each engagement*; the rules decide independently whether that breaches an SLA
  (and they flag strictly fewer engagements than were parked — a test asserts it).
- **foyle and joinery now carry parked cases too**, seeded alongside their existing
  structural delay/repetition/rework patterns (this is the cause of the detection
  movement above). Same circularity guard as advisory: each generator records only
  *where* a case was parked (stalled at a stage, left unowned, piled onto one
  owner); `detection/case_rules.py` decides independently whether that breaches an
  SLA, and flags strictly fewer cases than were parked. Neither drive carries a
  money column, so `unrealised_value` is out of reach for foyle/joinery **by
  design, not oversight** — a test pins that absence. This is what gives foyle and
  joinery a populated action queue (12 items each) instead of the thin,
  structural-only queue that was previously a P0 follow-up (TASKLIST.md).
- **Mapping-agent metrics (the headline eval):** role/column accuracy + column **F1** across three conditions — heuristic baseline → LLM → human-approved. Current results (`outputs/eval_mapping_<profile>.json`):

  | Profile | baseline F1 | LLM F1 | human F1 |
  |---|---|---|---|
  | foyle | 0.846 | 0.968 | 1.000 |
  | joinery | 0.308 | 0.909 | 1.000 |
  | advisory | 0.500 | 0.766 | 1.000 |

  Baseline collapses on joinery's renamed-header fork — that gap is the argument for the LLM audit; the human gate closes the residual. Advisory's LLM figure is the weakest of the three online conditions — 0.766 against foyle's 0.968 and joinery's 0.909 — driven by precision (0.621, 11 column errors) rather than recall (1.0): the LLM proposal over-includes columns on this profile rather than missing them. Written plainly, not smoothed over.

  Foyle's human-approved condition is a genuine, mixed result rather than a clean 1.000 across the board: column F1 is 1.000, but `role_accuracy` is only 0.6, because the approver labelled `host families 2026.xlsx` as `ignore` and `staff phone list.xlsx` as `notes`, where ground truth says `reference` and `ignore` respectively. Every column mapping was corrected; two file roles were not. That is a real finding about Gate 1, not a defect to fix: the human reviewer catches column-level semantics reliably but can still mislabel what a whole file is for.
- **Longitudinal replay (the "dynamic system" eval):** `synthetic/generate_stream.py`
  writes 9 cumulative weekly snapshots per profile (`stream_<p>/tick_NN/`) + a
  per-tick ground truth; `eval/replay.py` replays them through the unchanged
  pipeline core with a **simulated oracle Gate-2 approver** feeding the learning
  loop (the write-up must state the approver is an oracle, not a human). Two
  curves out (`eval/plot_replay.py` → `outputs/replay_*_<p>.png`): detection F1
  tracking a moving truth (incl. an honest gap-threshold wobble at joinery tick 6
  — precision dips, the gate rejects the FPs, F1 recovers), and a learned-fix
  retrieval curve intended to climb as *validated* fixes enter `sme_resolutions`
  — see the measured result immediately below, which is that it did not, in this
  9-tick window. Eval-side only: any replay-learned entries would be
  RES-RPL-prefixed in `outputs/`, dashboard state untouched; default `--fresh`
  reset keeps runs reproducible.

  **The replay is now outcome-gated too, and has been re-run under that rule for
  both profiles.** The oracle approves, "does" the work, and at a later tick the
  intervention is measured against that tick's analysis; only a measured
  improvement is validated and embedded. Each tick record carries both curves —
  `lifecycle.validated` (what the outcome-gated loop trusts) and
  `lifecycle.approved_unmeasured` (what the old approval-gated loop would have
  trusted by the same tick). The measured result: **`lifecycle.validated` stays at
  0 for the full 9-tick window, in both profiles** — no oracle-approved fix was
  ever completed and re-measured against a later tick showing genuine
  improvement, so nothing was promoted into `sme_resolutions`.
  `lifecycle.approved_unmeasured` climbs to **3** in both profiles (foyle by tick
  4, joinery by tick 6) and holds. The cause, traced to source: affected-case
  counts only *grow* as more of the recording is revealed (2→3→4 per
  intervention), so `actions/outcome.py::compare` can never return a measured
  improvement inside this window — interventions land either `ineffective` or
  inside the 10% noise band. `tests/test_replay.py` proves the validation path
  does work when a finding genuinely disappears, so the mechanism is sound; the
  replay simply cannot produce the counterfactual a measured improvement
  requires. The previously cited "learned-hit rate 0 → 1 over the run" does
  **not** survive outcome gating — under the new rule the honest end-of-replay
  state is 3 fixes approved and tracked, 0 proven to work. `replay_learned_<p>.json`
  is written only on a promotion, so it no longer exists for either profile;
  `outputs/replay_pending_<p>.json` and `outputs/replay_interventions_<p>.json`
  are the artefacts that substantiate both curves now.

  Second honesty note for the write-up: the stream is a *recording*, not a
  counterfactual. An intervention approved at tick t cannot change what tick
  t+1 contains, so a validated outcome there evidences the *measurement
  machinery*, not causation.
- RAG metrics: retrieval relevance (MRR or NDCG).
- Qualitative: structured expert walkthrough (2–3 people) on trust, usability, recommendation quality.

---

## 8. Mock Google Environment

A **mock Google Workspace** was built early on to demonstrate real API
connectivity *without* real data:

- GCP project: `foyle-mock-pipeline`
- Account: `foyle.mock.sme@gmail.com`
- APIs: Google Sheets, Google Drive; OAuth creds in `google_oauth.json`

**The code that used it is gone** (2026-08-05). It served the `--source sheets`
and `--source foyle-sheets` paths, both removed along with their readers — the
messy-drive flow reads local folders, and nothing had called the Sheets path in
a long time. The GCP project and credentials still exist outside the repo.

Write it up as what it was: an early connectivity spike that proved the
approach and was then superseded, not a component of the delivered system.

---

## 9. Ethics / Constraints baked into the code

- Human approves every corrective action (agency + accountability — aligns with ACM Code of Ethics on human oversight). **Both** gates enforce this.
- All execution testing happens in a **test environment only, never live systems**.
- **Zero raw PII to external LLM APIs:** every sample cell in the mapping-agent payload passes through `scrub.anonymise` first (there is a test asserting placeholders only). This is the implemented privacy control — cite it as such.
- Every executed action is **logged with a timestamp** for auditability (`decisions.jsonl`, `mapping_decisions.jsonl`).
- Transparency: the system always shows its working + the retrieved evidence behind a suggestion.
- **Secret handling:** `ANTHROPIC_API_KEY` lives in `bottleneck-ingest/.env` (gitignored). The key was pasted in a dev chat — **rotate it at the Anthropic console before submission.**

---

## 10. Working Conventions (how Emmett likes to build)

- **Plain language.** Technical depth is fine but shouldn't be foregrounded. Analogies for abstract ideas land well (plug adapters, abstract base classes).
- **Task-oriented.** Give directly-usable output; expect discrete, clear steps rather than sprawling refactors.
- **Phase-based, strict scope.** Guard against scope creep and over-engineering — minimum viable system first. Test each component independently, then again after integration.
- **Handoff style:** when producing a plan, tag steps `[AGENT]` (build tasks) vs `[YOU]` (human actions Emmett must do himself).
- **Diagrams before code** for anything architectural — Emmett thinks visually.
- Runs Claude Code in the VS Code sidebar, task-by-task, supervised.
- **Windows:** call `.venv/Scripts/python.exe` explicitly (bare `python` hits the Store stub); set `PYTHONIOENCODING=utf-8` when status values contain `✔`.
- **Mapping drift:** approving in the browser overwrites `mappings/approved_<profile>.json`. If eval numbers move unexpectedly, do **not** run `git checkout mappings/approved_*.json` — the drifted foyle mapping was itself committed (`c92caef`), so that command restores the drift, not the fix. Recover from the last known-good commit instead: `git checkout <good-commit> -- mappings/approved_<profile>.json` (foyle's known-good mapping is in `a8e3437`).

---

## 11. Current Status (2026-08-01)

- **Re-architecture milestones M0–M6 all shipped**, plus the product-grade upgrade:
  RAG diagnosis agent, LangGraph loop, **dynamic detection** (statistical, 0..N
  findings, no markers), learning loop, cost
  model, impact history + sparklines, zero-PII payload viewer.
- **Action layer shipped (2026-07-23)** — `actions/`: ActionItem / Intervention /
  InterventionOutcome / BusinessImpact / EvidenceReference / AnalysisSnapshot,
  a shared lifecycle, deterministic explainable ranking, JSON persistence, and
  `detection/case_rules.py` for case-level findings. The dashboard's primary
  view is now **Today** (an action queue), with the workflow map and bottleneck
  cards demoted to supporting evidence.
- **Two behavioural corrections landed** (§4a, §4b): execution is routed by
  action category so an approved operational fix can no longer trigger status
  normalisation, and the learning loop is outcome-gated so approval alone no
  longer creates trusted RAG guidance.
- **Generalisability demonstrated on three SMEs:** foyle + joinery + **advisory**
  (Northstar Advisory) through one pipeline, zero new reader/detector/action code
  for SME #3.
- **All four action-layer Priority-0 follow-ups are now closed** (TASKLIST.md):
  the mapping F1 table is reproducible from `outputs/` for all three profiles,
  the foyle mapping is back to its pre-drift state, the longitudinal replay has
  been re-run and re-cited under the outcome-gated learning loop, and foyle +
  joinery now carry parked operational cases so their action queues (12 items
  each) demonstrate the same worker-facing product advisory does.
- **Eval numbers produced:** mapping F1 table (§7, all three profiles online,
  no outstanding caveats); detection baseline-vs-dynamic across all three
  profiles, now measured against foyle/joinery's reseeded drives (§7); the
  longitudinal replay curves under outcome gating, measured for both profiles (§7).
- **§6a** — LangGraph is in the artifact. Ollama was trialled and withdrawn.
- **React dashboard (hitl-react) live** — **Today** (action queue, expandable
  evidence, owner/due-date, progress + outcome-review controls, "what was sent
  to the AI"), Mapping Review (Gate 1), pipeline stepper, workflow DAG,
  Bottlenecks, Fixes + remediation diff, SME switch, payload modal.
- Tests green: **284 passed** (`pytest -q`).
- **World simulator shipped (P1)** — `simulator/`: a per-SME world that advances
  a day at a time, renders the messy drive the product ingests through its
  existing `--drive` flag, and — the point — lets **approved ActionItems change
  what happens next**, so a finding's affected-case count can finally fall.
  Advisory wired; the product is unchanged and unaware. Spec and plan in
  `docs/superpowers/`. P2 (eval rewire) and P3 (dashboard demo) not started.
- Dissertation Word draft exists (`Murray_B00810618_Dissertation_Draft.docx`) — Sections 1–4 written, later phases scaffolded. **Sections describing detection/eval need updating for the dynamic detector, the action layer, and the reseeded/replay numbers above.**

**Fixed (was "a known, unfixed hazard"):** diagnosis prose is no longer joined
to findings by `bn.id`. `detection/detect.py::finding_key` derives a stable
content key (`type::stage::metric_label`); `actions/build.py` joins on it and
falls back to the positional id only for a wholly legacy export, and both
exporters emit it. `DetectedBottleneck.id` and `CaseFinding.id` are assigned by
**rank order** and must never be used as a join key across two analyses.

**On the horizon:** Phase 2–5 build reports; supervisor sign-off on the
consented Foyle export.

**Simplification pass, 2026-08-05.** ~2,100 lines removed. Every published
number was re-verified afterwards and none moved (detection macro-F1:
foyle 0.486/1.000, joinery 0.487/1.000, advisory 0.471/1.000 — identical to §7).

Removed: the five superseded ingest sources (`sheets`, `all`, `foyle`,
`foyle-tracker`, `foyle-tracker-sheets`, `foyle-sheets`) with their four
readers, two generators and three exporters; and the local-LLM anomaly pass.
`--source messy` and `--source local` remain.

**Deliberately kept, with reasons:**
- `--source local` — it pulls `excel_reader`/`text_reader`, imported
  non-lazily and reading the ops-notes text files. Removing it is a separate,
  more careful question than removing obviously-dead Foyle paths.
- `config.py`'s marker constants, `detect_all`, `bridge/export_cases.py` —
  these thread into `detection/detect.py::detect_generic`, which produces the
  **marker baseline** in §7's detection table. ~30 lines of dead strings is not
  worth risking a cited number. `export_cases.py` also still holds shared
  helpers that `export_messy.py` imports.
- `synthetic/generate_stream.py` — live until P2 retargets the replay.

**Still outstanding (write-up, not code):** §7's replay curves predate the
simulator. And the "SME #2 is a config block and zero engine code" claim is
**not yet fully true for `simulator/`** — `simulator/render.py` hard-codes
advisory filenames and generator method names. Do not overclaim it.
