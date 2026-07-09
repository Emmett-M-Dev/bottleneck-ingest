# CLAUDE.md

Context file for AI coding assistants working on this project. Read this first, every session.

> **Companion docs:** `HANDOVER.md` (current build state + how to run), `PHASE1_REPORT.md`,
> and the per-phase build reports. This file is the *why*; HANDOVER.md is the *what/how now*.

---

## 1. Project Snapshot

- **Title:** Agentic Workflow Optimisation for SME Operations — An End-to-End Pipeline for Bottleneck Detection, RAG-Based Diagnosis and Human-in-the-Loop Approval
- **Module:** COM748 MSc Research Project (Ulster University, MSc Artificial Intelligence)
- **Student:** Emmett Murray (B00810618)
- **Supervisor:** Dr Jose Santos
- **Case study / validation partner:** Foyle — an educational-tourism SME

**One line:** A lightweight, locally-run agentic pipeline that ingests messy SME data, detects operational bottlenecks, diagnoses them with RAG-grounded suggestions, and executes approved fixes — with a human approving every consequential action.

**Deadlines (hard):**
| Milestone | Date |
|---|---|
| Feature freeze | 1 Aug 2026 |
| Report submission | 24 Aug 2026 |
| Slides submission | 28 Aug 2026 |
| Viva | 31 Aug 2026 |

After feature freeze, no new features — only bug-fixing, evaluation, and writing.

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
`ingest.py --source messy --profile <p>`. Older sources (`foyle`, `foyle-tracker`,
`sheets`, `all`) still exist but are legacy.

---

## 3. Academic Framing (why the design choices exist)

The contribution is **democratising intelligent process automation for SMEs**, in deliberate contrast to AI consolidating around large enterprises. OECD data: ~40% of large firms use AI vs ~12% of small firms — the gap is driven by resource constraints (ROI uncertainty, no AI-ready data, skills gaps), not technical sophistication. So the system must be **lightweight and runnable without a specialist team**.

**The generalisability claim now rests on demonstrated evidence, not assertion.**
Two contrasting SMEs — **foyle** (educational placement) and **joinery** (trades /
fit-out) — run through the **identical** pipeline core. Onboarding the second SME
added a config block + an approved mapping and **zero new reader or detector code**.
That is the thesis payoff (see §4, §7). Any code that hard-codes SME-specific logic
into the pipeline core undermines it.

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
   │  1. Bottleneck detection (detect_generic: delay / repetition / rework)
   │  2. RAG diagnosis (over the resolution store)
   │  3. Fix suggestion
   └──────────┬───────────┘
              │  [HITL GATE 2 — Fixes: human approves / rejects / modifies each fix]
   ┌──────────▼───────────┐
   │  REMEDIATION EXECUTOR │  ← Carries out the approved data-remediation. Logged + timestamped.
   └──────────────────────┘
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
| Pipeline orchestration | **Plain sequential Python** | ingest → detect → export, driven by `ingest.py` + CLI modules. See §6a re: LangGraph. |
| Mapping-inference agent | **Claude API — `claude-opus-4-8`** | `audit/infer.py` only. `messages.parse` with a Pydantic schema. **No `temperature`** (400s on Opus 4.8). Zero raw PII in the payload (scrubbed first). |
| Vector store | **ChromaDB** | Powers the RAG resolution store. |
| Embeddings | **sentence-transformers** | |
| HITL UI (current) | **React** (`hitl-react`) | Vite + Tailwind + `@xyflow/react`. FastAPI backend in `hitl-react/api/` acts as a thin orchestrator — shells out to the pipeline venv. |
| HITL UI (legacy) | **Streamlit** (`hitl-interface`) | Superseded by hitl-react for the mapping/dashboard flow; kept for provider abstractions + fixtures. |
| File parsing | **openpyxl**, **pandas** | Excel + tabular. `engine="openpyxl"`. |
| Parquet | **fastparquet** (NOT pyarrow) | Pinned. See constraint below. |
| Email execution | **smtplib** | |
| Env | Python + venv (per repo) | Version-pinned. `bottleneck-ingest/.venv`; `hitl-react/api/.venv` (py3.14). |
| Dev in | Jupyter + VS Code | Task-by-task, supervised. |

**Parquet:** use **`fastparquet`**, NOT `pyarrow`. Importing pyarrow eagerly loads the
Arrow C++ runtime, which **segfaults in-process** with chroma/hnswlib + torch on Windows.
Pinned intentionally — do not "helpfully" swap it. Relatedly, `audit/` and `remediate/`
run as **separate processes** and must **never import chromadb / pyarrow / torch**.

### 6a. ⚠️ LangGraph & Ollama — claim vs build (RESOLVE BEFORE WRITE-UP)

The original design named **LangGraph** (orchestration) and **Ollama** (local dev LLM,
with cloud APIs reserved for eval only). **Neither is in the current codebase.** As built:
orchestration is sequential Python, and the one LLM call (the mapping agent) is **Claude
API**, used as a core runtime dependency — not eval-only.

This must be reconciled so the dissertation matches the artifact. Options:
1. **Update the framing** to describe what's built (sequential pipeline + Claude mapping
   agent) — lowest risk, honest.
2. **Implement LangGraph** as a thin orchestration wrapper if the "stateful graph" claim
   is load-bearing for the contribution — costs build time near freeze.
3. If Ollama/local-LLM privacy is a stated ethics point, either wire a local model for
   the mapping agent or re-frame the privacy story around the **zero-PII scrub** (which
   IS implemented) instead of local inference.

Do not let the report assert LangGraph/Ollama while the repo has neither.

---

## 7. Data Strategy — synthetic-first, ground-truth-by-design

- Datasets are **synthetic**, one messy drive per SME profile (`messy_foyle`, `messy_joinery`).
- **3 bottleneck types** (delay / repetition / rework), **injected by design**, so ground truth is known before detection runs. The count is fixed by the 3 detectors — more data raises affected-counts, it does not spawn new bottleneck *types*.
- **Circularity guard:** generating *and* evaluating on data designed by the same person is a validity risk. Injection logic (`synthetic/generate_messy_*.py`) and detection logic (`detection/detect.py`) are cleanly separated; the detector does not know the injection rules.
- Detection metrics: precision / recall against the known injected bottlenecks.
- **Mapping-agent metrics (the headline eval):** role/column accuracy + column **F1** across three conditions — heuristic baseline → LLM → human-approved. Current results (`outputs/eval_mapping_<profile>.json`):

  | Profile | baseline F1 | LLM F1 | human F1 |
  |---|---|---|---|
  | foyle | 0.846 | 0.968 | 1.000 |
  | joinery | 0.308 | 0.909 | 1.000 |

  Baseline collapses on joinery's renamed-header fork — that gap is the argument for the LLM audit; the human gate closes the residual.
- RAG metrics: retrieval relevance (MRR or NDCG).
- Qualitative: structured expert walkthrough (2–3 people) on trust, usability, recommendation quality.

---

## 8. Mock Google Environment

A **mock Google Workspace** exists to demonstrate real API connectivity *without* real data:

- GCP project: `foyle-mock-pipeline`
- Account: `foyle.mock.sme@gmail.com`
- APIs: Google Sheets, Google Drive; OAuth creds in `google_oauth.json`

Used by the legacy `--source sheets` / `foyle-sheets` paths. The current messy-drive
flow reads local folders, not Sheets — the mock is a connectivity demo, not the primary
ingest path.

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
- **Mapping drift:** approving in the browser overwrites `mappings/approved_<profile>.json`. If eval numbers move unexpectedly, `git checkout mappings/approved_*.json`.

---

## 11. Current Status (2026-07-09)

- **Re-architecture milestones M0–M6 all shipped.** Mapping-inference agent + second HITL gate + two SME profiles, end-to-end.
- **Generalisability demonstrated:** foyle + joinery through one pipeline, zero new reader/detector code for SME #2.
- **Eval numbers produced** (F1 table in §7); `outputs/eval_mapping_*.json` committed.
- **React dashboard (hitl-react) live** — Mapping Review (Gate 1), workflow DAG with per-stage source-sheet hover, Bottlenecks, Fixes (Gate 2) + remediation, SME profile switcher.
- Both repos committed & clean on `master`. `HANDOVER.md` written.
- Foyle drive at its reproducible 5-file state; tests green (`pytest -q`).
- Dissertation Word draft exists (`Murray_B00810618_Dissertation_Draft.docx`) — Sections 1–4 written, later phases scaffolded.

**On the horizon:** reconcile §6a (LangGraph/Ollama claim vs build) before write-up;
Phase 2–5 build reports; supervisor sign-off on the consented Foyle export; final
dissertation refinement pass. Feature-complete otherwise — default is to pivot to writing.
