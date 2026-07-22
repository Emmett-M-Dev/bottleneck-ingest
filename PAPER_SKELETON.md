# Paper Skeleton — 8-page research paper

**Working title:** Democratising Intelligent Process Automation for SMEs: An
Agentic, Human-in-the-Loop Pipeline for Bottleneck Detection and RAG-Grounded
Diagnosis

**Student:** Emmett Murray (B00810618) · **Module:** COM748 (Ulster, MSc AI) ·
**Supervisor:** Dr Jose Santos

> **Format assumption:** IEEE/ACM **two-column**, 10pt — ~800–950 words/page →
> ~6.5–7.5k words of body + refs on page 8. If it's single-column 1.5-spaced
> instead, halve the per-page word counts and cut Related Work + Discussion
> depth. Page budgets below are for the two-column case. Adjust if the brief
> differs.

---

## Page budget at a glance

| § | Section | Pages | Words |
|---|---|---|---|
| — | Abstract + keywords | 0.2 | ~200 |
| 1 | Introduction | 1.0 | ~800 |
| 2 | Related work / background | 0.75 | ~600 |
| 3 | System design (the 3-layer model) | 1.5 | ~1200 |
| 4 | Implementation | 1.25 | ~1000 |
| 5 | Evaluation | 1.75 | ~1400 |
| 6 | Discussion — limitations & ethics | 0.75 | ~600 |
| 7 | Conclusion & future work | 0.4 | ~350 |
| — | References | 0.4 | — |
| **Σ** | | **~8.0** | |

Figures/tables eat ~1.5 pages of that — budget the prose accordingly.

---

## Abstract (~200 words)
- One-sentence problem: SMEs are locked out of intelligent process automation
  (IPA) — OECD ~40% large-firm vs ~12% small-firm AI adoption, driven by
  resource constraints, not sophistication.
- What was built: a lightweight, locally-run **agentic** pipeline that ingests
  messy SME spreadsheets, detects operational bottlenecks **dynamically**,
  diagnoses them with **RAG-grounded** suggestions, and executes only
  **human-approved** fixes — two HITL gates.
- Headline evidence: generalisability across two contrasting SMEs with zero new
  core code; mapping-agent F1 0.31→0.91→1.00 (baseline→LLM→human); detection
  macro-F1 0.52→1.00 (marker baseline→dynamic).
- Claim: the contribution is *democratisation* — the same core onboards a new
  SME via a config block + one approved mapping.

## Keywords
SME automation · human-in-the-loop · RAG · process mining · bottleneck
detection · agentic pipeline · schema mapping · privacy-preserving LLM

---

## 1. Introduction (~1 page)
- **1.1 The gap.** IPA is consolidating around large enterprises; the barrier
  for SMEs is resource (ROI uncertainty, no AI-ready data, no specialist team),
  not technical need. Cite OECD adoption figures.
- **1.2 Why SME data is the hard part.** Real ops data is *sedimented*: a sheet
  used for months, forked for a new season with renamed headers, stale copies
  lingering, freetext statuses, no controlled vocabulary. (Grounded in the Foyle
  SharePoint audit — motivate the "messy drive" model.)
- **1.3 Contribution list** (bullet, 4–5 items):
  1. A three-layer pipeline whose **core is invariant across SMEs** — the
     generalisability claim, demonstrated not asserted (foyle + joinery).
  2. A **dynamic, config-free bottleneck detector** (statistical scan) that
     beats a marker baseline (macro-F1 0.52→1.00).
  3. A **mapping-inference agent** + first HITL gate that turns messy schemas
     into a canonical contract (F1 0.31→0.91→1.00).
  4. A **RAG diagnosis agent** grounded in a curated resolution store, with a
     **learning loop**: approved fixes become retrievable knowledge.
  5. Implemented **privacy + oversight controls**: zero-PII scrub, local
     inference for exploratory analysis, human approval at every consequential
     action.
- **1.4 Scope boundary** (one line): dissertation artifact = local, synthetic,
  file-based; live deployment is out of scope.
- **Figure 1:** the three-layer architecture diagram (plug-adapter framing).

## 2. Related work / background (~0.75 page)
- **2.1 Process mining / bottleneck detection** — classical event-log mining
  (α-algorithm, performance analysis); contrast: they assume a *clean* event
  log exists. Position this work as "everything before the event log."
- **2.2 Schema matching / data integration** — LLMs for column mapping;
  position the human gate + zero-PII scrub as the novel wrap.
- **2.3 RAG + agentic systems** — retrieval-grounded generation, tool-use
  agents, LangGraph; HITL / human oversight literature (ACM Code of Ethics).
- **2.4 AI for SMEs** — the democratisation framing; what's missing = a system
  that assumes messy data and no specialist team.

## 3. System design (~1.5 pages)
- **3.1 The three-layer model.** Adapter (thin, per-SME) → canonical schema
  (invariant) → fixed core (detection/RAG/fixes) → remediation executor. The
  plug-adapter analogy. The canonical `Event` contract is the load-bearing
  interface.
- **3.2 Two HITL gates** (chronological): Gate 1 = Mapping Review (confirm
  proposed schema before ingest); Gate 2 = Fix Approval (nothing executes
  unapproved). Both log corrections + time-to-decision — the measurable HITL
  numbers.
- **3.3 Dynamic detection.** Statistical scan of every stage: delay (outlier
  gaps vs the log's own Q3+1.5·IQR), repetition (re-entry w/o progress), rework
  (backward transition vs stage order). 0..N findings — count is a property of
  the data. **Figure 2:** worked example on one stage.
- **3.4 RAG diagnosis + learning loop.** Retrieve top-k past resolutions →
  scrubbed evidence payload → structured diagnosis. Approved Gate-2 fixes are
  written back into the resolution store (knowledge curation, not just a safety
  valve). **Figure 3:** the loop.
- **3.5 Hybrid local/cloud division of labour.** Claude for the two precision
  tasks (mapping, diagnosis) with the zero-PII scrub; local Ollama model for the
  exploratory anomaly pass (zero marginal cost, payload never leaves the box).

## 4. Implementation (~1.25 pages)
- **4.1 Stack & orchestration.** LangGraph StateGraph (detect→retrieve→
  diagnose→gate→execute); the two-phase gate (run pauses at `awaiting_gate`,
  resume re-enters at the gate — the run-state JSON is the audit artifact).
- **4.2 Canonical store vs knowledge store.** event_log.parquet (what's
  happening) vs the ChromaDB `sme_resolutions` collection (how problems were
  fixed). Keep them separate.
- **4.3 The mapping-inference agent.** headers + ≤5 scrubbed sample rows →
  structured `messages.parse` → proposed mapping + mess report.
- **4.4 Remediation executor.** freetext status → controlled vocab
  {Complete, Open, N/A}; cleaned *copies*, originals untouched; before→after
  diff.
- **4.5 Engineering constraints worth a sentence.** process isolation
  (chroma/torch never share a process with the executor/mapping agent);
  fastparquet-not-pyarrow; graceful degradation (no Ollama = anomaly pass
  silently skips).
- **Table 1:** per-SME "what changes" — config block + approved mapping vs the
  invariant core (the generalisability evidence, in one table).

## 5. Evaluation (~1.75 pages)
- **5.1 Method & data.** Synthetic-first, ground-truth-by-design; the
  circularity guard (injection logic and detection logic are separate modules;
  the detector doesn't know the injection rules).
- **5.2 Detection.** P/R/F1 per pattern type, marker baseline vs dynamic.
  **Table 2:** foyle 0.524→1.000, joinery 0.523→1.000. Argument: presence-based
  markers collapse on structural repetition/rework.
- **5.3 Mapping agent (headline).** F1 across baseline→LLM→human. **Table 3:**
  foyle .846→.968→1.000, joinery .308→.909→1.000. The joinery baseline collapse
  on the renamed-header fork is the argument for the LLM; the human gate closes
  the residual.
- **5.4 Generalisability.** SME #2 (joinery) onboarded with a config block + one
  approved mapping, zero new reader/detector code — the thesis payoff.
- **5.5 HITL cost.** correction counts + time-to-decision per gate (from the
  decision logs).
- **5.6 Longitudinal dynamics (the "living system" eval).** `eval/replay.py`
  replays the drive as 9 weekly snapshots through the *unchanged* core, with a
  simulated oracle Gate-2 approver feeding the learning loop. Two curves,
  **Figure 5:**
  - *Learning-loop payoff* — learned-hit-rate climbs **0 → 0.67 → 1.00** (foyle,
    ticks 3–5) as approved fixes accumulate; mean learned rank → 1.0. The fix
    approved at tick t is retrieved for the same bottleneck at t+1.
  - *Detection under partial data* — macro-F1 sits at 0.67 in the early ticks
    (too few cases cross the min-affected threshold) then stabilises at 1.00 as
    the log fills. Honest, and it shows the detector is data-driven, not tuned.
  - Isolation note for the write-up: replay uses its own learned file +
    decision log (`replay_learned_*`, `replay_decisions_*`) and a fresh corpus
    reset — the dashboard's real state is never touched.
- **5.7 (if space) RAG retrieval quality** — MRR/NDCG with profile+type match
  as ground-truth relevance (the per-tick `mean_learned_rank` already gives
  this for the learned entries).

## 6. Discussion — limitations & ethics (~0.75 page)
- **Limitations** (be honest — examiner credit): synthetic data authored by the
  evaluator (mitigated by the injection/detection separation but still a
  validity threat); LLM confidence is uncalibrated (report it, don't lean on
  it); timing data from dev runs; two-SME sample; anomaly-pass findings are
  advisory, not evaluated against ground truth.
- **Ethics as implemented, not aspirational:** human approval at both gates
  (agency/accountability); zero-PII-to-API scrub (with the asserting test);
  timestamped audit logs; transparency (system always shows retrieved
  evidence). Tie each to the ACM Code where relevant.

## 7. Conclusion & future work (~0.4 page)
- Restate: democratisation via an invariant core; evidence not assertion.
- Future: more SMEs / real (consented) export; calibrated confidence; the live
  product boundary (real connectors) explicitly deferred.

## References (~0.4 page)
- OECD SME AI adoption; process-mining classics (van der Aalst); RAG (Lewis
  et al.); schema matching survey; LangGraph/agentic; ACM Code of Ethics;
  ChromaDB / sentence-transformers / the Claude model. Target 12–18 refs.

---

## Figures & tables checklist (build these first — they anchor the prose)
- **Fig 1** three-layer architecture (adapt the CLAUDE.md ASCII to a clean vector)
- **Fig 2** dynamic-detection worked example (one stage, gap distribution + threshold)
- **Fig 3** RAG diagnosis + learning loop
- **Fig 4 (optional)** dashboard screenshot montage (Mapping Review, Bottlenecks, Fixes)
- **Table 1** what-changes-per-SME (generalisability)
- **Table 2** detection P/R/F1 baseline vs dynamic
- **Table 3** mapping F1 baseline→LLM→human

---

## EXTRA MATERIALS / SUPPLEMENTARY (not counted in the 8 pages)

Everything that strengthens reproducibility/credibility but won't fit. Group
into an appendix or a linked repo release.

**A. Reproducibility**
- Public/committed code repo (both repos), commit hashes, `requirements.txt`
  pinned stack, exact run commands (from HANDOVER.md).
- Seeds + the synthetic-drive generators (`generate_messy_*.py`) — anyone can
  regenerate the exact datasets.
- The eval scripts (`eval/score_mapping.py`, `eval/score_detection.py`) and
  their raw JSON outputs.

**B. Full evaluation artifacts**
- Complete per-SME eval JSONs (mapping + detection), not just the headline rows.
- The ground-truth files (`ground_truth_messy_*.json`) + the mapping ground
  truth.
- HITL decision logs (`mapping_decisions.jsonl`, `decisions.jsonl`) — the raw
  timing/correction data behind §5.5.
- Detection calibration table (LLM confidence vs retrieved-type match).

**C. System detail**
- The per-phase build reports (PHASE1_REPORT.md etc.) as the engineering log.
- Prompt templates (mapping-inference system prompt, diagnosis system prompt,
  anomaly-pass prompt) + the Pydantic output schemas.
- An example scrubbed LLM payload (the "what the AI saw" JSON) — concrete proof
  of the zero-PII control.
- The canonical `Event` / `NormalisedRecord` schema.

**D. Qualitative**
- Dashboard walkthrough screenshots or a short screen-capture (the demo script):
  Mapping Review → workflow DAG → Bottlenecks (incl. anomaly + RAG grounding) →
  Fixes → remediation diff → SME switch.
- Structured expert-walkthrough notes (2–3 people) on trust/usability/quality,
  if collected.

**E. Ethics/governance**
- The zero-PII test (assertion that only placeholders reach the API).
- Data-handling statement: synthetic data, no live credentials, consented
  export only, key rotation before submission.

---

## Open questions to resolve before writing
1. Exact format/length rule of the brief (2-col vs 1-col) — sets the word budget.
2. Is this the *standalone* 8-page paper, or a condensed version of the full
   dissertation? (Changes how much Related Work vs System detail to carry.)
3. Which evals make the cut if space is tight — detection + mapping are
   non-negotiable; RAG retrieval quality (§5.6) is the first cut.
4. Are expert-walkthrough qualitative results available, or is eval
   quantitative-only?
