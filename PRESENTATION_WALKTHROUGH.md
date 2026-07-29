# Presentation Walkthrough — every screen, every number, every source

Companion to `DEMO.md` (the copy-pasteable commands). This file answers the
question an examiner will actually ask: **"where did that number come from?"**

Written to be read aloud. Each screen gets: what you click → what appears →
what produced each thing on it.

---

## 0. The provenance legend

Every figure in the dashboard is one of six things. Memorise these six tags —
the rest of this document is just applying them.

| Tag | Meaning | Where it lives |
|---|---|---|
| **[CLAUDE]** | A call to the Anthropic API, model `claude-opus-4-8` | 3 call sites only (§2) |
| **[LOCAL]** | A call to a local Ollama model, `qwen2.5:7b` | 1 call site only (§2) |
| **[COMPUTED]** | Deterministic Python/pandas over the event log. Same input → byte-identical output | `detection/`, `actions/` |
| **[CONFIG]** | A number Emmett typed into `config.py` for that SME | `MESSY_PROFILES[<p>]` |
| **[TEMPLATE]** | An English sentence hand-written in Python, with numbers interpolated | `actions/templates.py`, `bridge/export_messy.py` |
| **[UI]** | Pure presentation — colour, layout, animation. Makes no claim about the data | `hitl-react/src/` |

Two more you will say out loud a lot:

- **measured** = summed from a value that is physically written in the SME's own
  spreadsheet.
- **projected** = derived from a [CONFIG] assumption. The UI labels these
  differently on purpose; `BusinessImpact.is_projection` is the flag.

---

## 1. The one-paragraph pitch, then the diagram

> Small firms run on spreadsheets. This system reads a messy folder of them,
> works out what needs attention today, shows the evidence behind each item down
> to the spreadsheet row, lets a human approve or reject it, and then measures —
> against a *later* snapshot of the same drive — whether the fix actually worked.
> A human approves at two gates, and nothing becomes trusted advice until it has
> been proven.

```
data/synthetic/messy_advisory/*.xlsx        ← 7 deliberately messy spreadsheets
        │
        ▼  audit/            [CLAUDE or heuristic]   proposes how files/columns map
outputs/ui_mapping_proposal_advisory.json
        │
        ▼  ══ HITL GATE 1 — Mapping Review tab ══    human confirms/edits
mappings/approved_advisory.json  (+ mapping_decisions.jsonl = the HITL metric)
        │
        ▼  ingest.py --source messy --profile advisory
           scrub PII → canonical Event rows → records.jsonl → ChromaDB → event_log.parquet
        │
        ├─▶ detection/dynamic.py     [COMPUTED]  which STAGES are broken
        ├─▶ detection/case_rules.py  [COMPUTED]  which CASES need touching
        ├─▶ detection/anomaly.py     [LOCAL]     advisory "AI-spotted" cards
        └─▶ pipeline/diagnose.py     [CLAUDE]    RAG diagnosis over past resolutions
        │
        ▼  actions/  →  evidence + impact + owner + due date + rank
outputs/ui_actions_advisory.json     ← THE ACTION QUEUE (the "Today" tab)
        │
        ▼  ══ HITL GATE 2 — approve / reject / dismiss ══
        │
        ├─ data_quality + machine-safe → remediation executor → CLEANED COPIES
        └─ everything else             → tracked Intervention, no file touched
        │
        ▼  re-ingest a LATER snapshot → measure → human validates
        ▼  only then → data/learned/learned_resolutions_advisory.json → ChromaDB
```

---

## 2. The four LLM call sites — and nothing else is AI

This is the slide that answers "how much of this is actually AI?". There are
**exactly four** places in the entire codebase where a language model is called.

| # | What | Model | File | Fires when | Fires from the dashboard? |
|---|---|---|---|---|---|
| 1 | **Mapping inference** — reads headers + 5 scrubbed sample rows per sheet, proposes role + column→field mapping + a cross-file "mess report" | `claude-opus-4-8` | [audit/infer.py](audit/infer.py) | `python -m audit.run --profile X` **without** `--offline` | **No.** Terminal only. |
| 2 | **RAG diagnosis** — per detected bottleneck: retrieve nearest past resolutions from ChromaDB, ask for diagnosis + root cause + fix + confidence | `claude-opus-4-8` | [pipeline/diagnose.py](pipeline/diagnose.py) | inside `bridge.export_messy` when neither `--offline` nor `DIAGNOSE_OFFLINE=1` | **Yes** — a profile switch runs `export_messy`. This is why gotcha #12 says set `DIAGNOSE_OFFLINE=1` for demos. |
| 3 | **Status value-map** — maps freetext status values to `{Complete, Open, N/A}` | `claude-opus-4-8` | [remediate/propose.py:51](remediate/propose.py#L51) | `python -m remediate.run --profile X --llm` | **No.** The API never passes `--llm`, so the Fixes tab always shows the **rule-based** map. |
| 4 | **Anomaly pass** — reads per-stage aggregate statistics, proposes patterns the three structural detectors don't look for | `qwen2.5:7b` via Ollama, local | [detection/anomaly.py](detection/anomaly.py) | inside `bridge.export_messy` when not offline **and** Ollama is running | Yes, same trigger as #2. Silently returns `[]` if Ollama is absent. |

**Everything else is deterministic code.** Detection, ranking, impact
arithmetic, the lifecycle state machine, the outcome verdict — none of them call
a model. That is a deliberate design claim, not a limitation: a worker can
disagree with the ordering on the merits because the ordering is arithmetic they
can read.

### Where LangGraph actually is — say this precisely

[pipeline/agent.py](pipeline/agent.py) is a real LangGraph `StateGraph`
(`langgraph==1.2.8`, pinned in [requirements.txt:42](requirements.txt#L42)):

```
START ──(no diagnoses)──> detect → retrieve → diagnose ─┐
  └──(diagnoses present)──────────────────────────────> gate
            gate ──approved──> execute → END
            gate ──otherwise────────────> END   (awaiting_gate | rejected)
```

Gate 2 is a **conditional edge**. A fresh run pauses at `awaiting_gate` and
writes `outputs/agent_run_<profile>_<ts>.json`; `--resume` reads the dashboard's
`decisions.jsonl` and re-enters at the gate.

> **The honest sentence:** the dashboard does **not** drive the LangGraph loop.
> The FastAPI layer orchestrates the same steps as separate CLI subprocesses.
> The graph is a second, auditable entry point over the identical pipeline core.
> Demo it in a terminal — `python -m pipeline.agent --profile foyle --offline` —
> and show the run-state JSON. Do not claim the browser is running it.

**There is no LangChain in this project.** `grep langchain requirements.txt`
returns nothing. If someone says "LangChain", correct them to LangGraph.

### The privacy control, in one sentence

Every free-text cell that goes to Claude passes through
[scrub/anonymise.py](scrub/anonymise.py) first — spaCy NER replaces
PERSON/ORG/GPE/LOC/FAC/NORP, regex catches emails, phones, UK postcodes. There
is a test asserting the audit payload contains placeholders only. The **"what
the AI saw" modal in the dashboard renders that exact payload with the
placeholders highlighted** — that is the control made visible, and it is your
strongest single demo moment.

---

## 3. Pre-flight — do this the night before, not on the day

```powershell
cd c:\Users\Emmet\bottleneck-ingest
$env:PYTHONIOENCODING = "utf-8"        # status values contain ✔; cp1252 cannot encode it
$PY = ".venv/Scripts/python.exe"
```

**Decide one thing first: do you want live Claude diagnoses in the Bottlenecks
tab?** They cost one API call per bottleneck and take 30–90 s each.

```powershell
# YES — bake them in beforehand, so the demo is instant AND shows real AI text:
$PY -m bridge.export_messy --profile advisory        # runs Claude + Ollama
Copy-Item outputs/ui_cases.json outputs/ui_cases_advisory.json -Force
Copy-Item outputs/ui_workflow.json outputs/ui_workflow_advisory.json -Force
```

Then, in the API terminal, **always**:

```powershell
$env:DIAGNOSE_OFFLINE = "1"        # so a live profile switch never re-bills you
$env:OLLAMA_MODEL = "qwen2.5:1.5b" # 7b needs ~6 GB RAM; use 1.5b on the 8 GB laptop
```

Start the two servers (this ordering matters):

```powershell
# terminal 1 — API
cd ../hitl-react/api ; .venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
# terminal 2 — UI
cd ../hitl-react ; npm run dev        # http://localhost:5173  (NOT 127.0.0.1 — Vite binds IPv6)
```

Sanity-check **exactly one** uvicorn is listening — two instances silently split
requests and the dashboard shows alternating stale/fresh data:

```powershell
netstat -ano | Select-String ":8000\s+.*LISTENING"      # expect ONE pid
```

**Current state of the repo, so nothing surprises you:**

| Thing | Right now |
|---|---|
| Active profile | `foyle` (`outputs/active_profile.txt`) |
| Event log holds | `foyle` (`outputs/event_log_profile.txt`) |
| foyle mapping proposal | **offline/heuristic** — badge will read 📐, mess report empty |
| joinery mapping proposal | **LLM, claude-opus-4-8** — badge reads 🤖, mess report populated |
| advisory mapping proposal | **offline/heuristic** — badge reads 📐 |
| foyle approved mapping | drifted; scores F1 **0.800**, not 1.000. `git checkout mappings/approved_foyle.json` restores it |
| Interventions for advisory | **zero** — the "What we did about it" board will be empty until you approve something live |

**Therefore: open Gate 1 on `joinery`, not on advisory.** It is the only profile
whose committed proposal is a genuine Claude output, so it is the only one where
the mess report and the per-column confidences are real AI. Say so out loud.

---

## 4. Screen by screen

### 4.1 The header bar

**You click:** nothing yet.

| Element | Source |
|---|---|
| Green square with `N` initial, "Northstar Advisory", "Lead-to-cash" | **[CONFIG]** `MESSY_PROFILES["advisory"]["ui"]` — brand / context / initials |
| "Live" pill, green, pulsing dot | **[COMPUTED]** `is_live` = does `outputs/ui_cases*.json` exist. The pulse is **[UI]** — it fires on each 10 s poll ([usePolling.js](../hitl-react/src/hooks/usePolling.js)). It is *not* a websocket, and it is not proof of freshness. |

**You click:** the brand wordmark → dropdown listing all three SMEs.

| Element | Source |
|---|---|
| Each row's brand/context | **[CONFIG]**, fetched by `GET /api/profiles`, which literally shells `python -c "import config; print(json.dumps(...))"` |
| "no approved mapping" (greyed row) | **[COMPUTED]** `config.approved_mapping_path(p).exists()` — a profile with no human-approved mapping cannot be activated. That is Gate 1 enforced at the UI level. |

**You click:** a different SME → full-screen overlay.

> "Switching SME profile — Foyle International → Northstar Advisory. Same
> pipeline core · new config block + approved mapping · zero new code."

That caption is **[UI]** text, but it is the thesis claim, so pause on it. Behind
it, `POST /api/profiles/{p}/activate` either serves a cached export (instant) or
re-runs `ingest.py → bridge.export_messy` (up to 900 s timeout). When it lands
you get a two-column KPI comparison: left = the previous SME's KPIs held in React
state, right = the new SME's, live. Both **[COMPUTED]**.

> **Line to say:** "Nothing in the pipeline changed between those two columns.
> Onboarding SME #3 added a config block, a synthetic drive and an approved
> mapping — no new reader, detector, action or ranking code."

---

### 4.2 The pipeline stepper (the strip under the tabs)

**You click:** any step → it jumps you to the tab where that stage lives.

Six steps: `Audit → Mapping Review (HITL) → Ingest & Detect → RAG Diagnose →
Fix Approval (HITL) → Remediate`.

All six statuses are derived **client-side from data already fetched** — no extra
API calls ([PipelineStepper.jsx](../hitl-react/src/components/layout/PipelineStepper.jsx)):

| Step | Goes green when |
|---|---|
| Audit | any `ui_mapping_proposal_*.json` exists |
| Mapping Review | the workflow has nodes — and since `ingest.py` **hard-errors** without an approved mapping, a populated workflow *proves* the gate was passed |
| Ingest & Detect | same condition |
| RAG Diagnose | `cases.length > 0` |
| Fix Approval | every case has been decided this session (localStorage) |
| Remediate | you have applied the cleanup this session (localStorage) |

> **Honesty flag to volunteer before you are asked:** "RAG Diagnose" going green
> means cases exist, **not** that Claude was called. When `DIAGNOSE_OFFLINE=1`
> the cases are template-built and the step still turns green. The place that
> tells the truth is the Bottlenecks card's confidence bar and the "what the AI
> saw" modal.

---

### 4.3 Tab: **Today** — the product

This is the primary view. Everything else is supporting evidence.

#### The impact strip (four tiles)

**You click:** nothing. Read them left to right.

| Tile | Value source | Measured or projected |
|---|---|---|
| **Needs attention** — `14` "across 26 engagements" | **[COMPUTED]** count of items in status proposed/approved/assigned/in_progress; the case count is the union of `affected_case_ids` | measured |
| **Revenue at risk** — `£481,000` "from the values in your own sheets" | **[COMPUTED]** sum of `impact.revenue_at_risk`. For `unrealised_value` items it is literally the sum of the `value` column read out of the SME's spreadsheets. For SLA-breach/stalled/unowned items it falls back to `avg_case_value` **[CONFIG £18,000]** for any case with no recorded value | **mixed** — `is_projection` is set per item; the card shows which |
| **Cost of delay so far** — `£10,826` "projected from your cost assumptions" | **[CONFIG × COMPUTED]** `affected × metric_days × costs.delay_day_cost` where `delay_day_cost = 120` is a number in `config.py` | **projected** |
| **Staff time at risk** — `42.5 h` | **[CONFIG × COMPUTED]** `affected × hours_per_repetition (4.0)` / `hours_per_rework (12.0)` | **projected** |

Below the tiles:

- *"Analysis of your drive as it stood on 2026-07-20"* — **[COMPUTED]**, and this
  is a nice detail to point out: it is the **newest event timestamp in the log**,
  not the wall clock ([case_rules.py:90](detection/case_rules.py#L90)). On
  synthetic data that keeps every run reproducible.
- *"Data confidence 97%"* — **[COMPUTED]** from three cheap signals in
  [build.py:63](actions/build.py#L63): events with no owner, events with an
  unparseable date, cases with only one event. The weights (0.4 / 0.4 / 0.2) are
  hardcoded. Hover-free but the sentence explaining it is printed by the CLI.
- *"7 case actions · 6 process changes · 1 data fix"* — **[COMPUTED]** counts by
  `action_category`.

#### The section order

Five sections, order fixed in code, an item is claimed by the **first** section
it matches so nothing is actioned twice:

`Revenue at risk → Capacity constraints → Delivery risks → Data quality → Everything else`

Server-side in [export_actions.py:112](bridge/export_actions.py#L112), mirrored
client-side in [constants.js:97](../hitl-react/src/constants.js#L97).

> Note the comment at line 109: capacity gets first refusal *before* delivery,
> because every capacity finding also carries a delivery risk — without that
> ordering the capacity section renders empty. Worth mentioning as evidence of
> real debugging.

#### Within a section — the ranking

**[COMPUTED], deterministic, no LLM.** [actions/rank.py](actions/rank.py):

```
score = 40 × (money / biggest money in this queue)
      + 20 × delivery risk    (high 1.0 / medium 0.55 / low 0.2)
      + 20 × (reach / widest reach in this queue)
      + 20 × urgency          (overdue 1.0 / today 0.85 / ≤3d 0.65 / ≤7d 0.45 / else 0.25)
      × detection_confidence  (scales the whole thing DOWN)
      × status multiplier     (proposed 1.0 → in_progress 0.5 → rejected 0.0)
```

The weights are hardcoded. Every item carries a `rank_explanation` — a list of
sentences saying where each point came from. Two runs over the same queue produce
byte-identical ordering; there is a test for it.

> **Line to say:** "The ranking is arithmetic, not a model. That is deliberate —
> a worker who disagrees with the order can see exactly which assumption to
> argue with."

#### One card, collapsed

Top item on advisory: **"16 case(s) worth £288,600 not yet billed"**, rank 78.7.

| Element | Source |
|---|---|
| Green **"Case action"** pill | **[TEMPLATE]** the `category` field of the `unrealised_value` template in [templates.py:121](actions/templates.py#L121) |
| Amber **"edits copies of your files"** pill (data-fix card only) | **[COMPUTED]** `ActionItem.is_machine_executable` — true only for templates on `MACHINE_EXECUTABLE_TEMPLATES` |
| **£288,600 measured** | **[COMPUTED]** summed from the `value` column of the sheets; `is_projection=False` because *every* case brought its own real figure ([impact.py:150](actions/impact.py#L150)) |
| "16 engagements" | **[COMPUTED]** |
| "due 2026-07-22" | **[CONFIG]** analysis date + `due_days["case_action"] = 2` for advisory |

#### One card, expanded — go through it in this order

1. **The summary sentence.** For a case-rule item this is `finding.detail` — a
   **[TEMPLATE]** f-string with **[COMPUTED]** numbers, e.g. *"These cases carry a
   value but have not reached a revenue stage. Every day they sit there is cash
   not in the bank."* For a **structural** item (delay/repetition/rework) this is
   the **[CLAUDE]** diagnosis text if `export_messy` ran online, otherwise the
   authored fallback in [export_messy.py:45](bridge/export_messy.py#L45).

2. **"Why these were picked"** — the evidence list. **[COMPUTED]**, and this is
   the bit to linger on: each row ends with `· proposals 2026.xlsx, row 34`. That
   is a real `source_ref` of the form `file:sheet:row`, carried all the way from
   ingestion. **You can open the spreadsheet and check it.**

3. **Affected engagements** — monospace chips, **[COMPUTED]** case ids.

4. **Recommended action + numbered steps** — **[TEMPLATE]**, from
   [templates.py](actions/templates.py). *Unless* a Claude diagnosis came back for
   a structural item, in which case Claude's `suggested_fix.summary` and `steps`
   replace them ([build.py:213](actions/build.py#L213)) — **but never the
   category.** Routing stays the system's decision, not the model's.

5. **Impact line** — `Revenue at risk — 16 case(s) summed at their recorded
   value`. That trailing clause is `BusinessImpact.basis`: **the arithmetic in
   words**, generated alongside the number. Rule 1 of `impact.py`: every figure
   is explainable.

6. **Confidence line** — `detection 95%, data 97%`.
   > **Honesty flag:** `detection 95%` is **hardcoded per finding type** in
   > `_BASE_CONFIDENCE` at [build.py:39](actions/build.py#L39) — 0.99 for
   > "unowned case" (near-certain by construction), 0.95 for an SLA breach, 0.4
   > for an LLM anomaly. It is a calibration constant expressing how inferential
   > each rule is, **not** a learned probability. Only a structural item that got
   > a live Claude diagnosis carries a model-supplied confidence.

7. **The execution notice** — the coloured strip. This is the correction that
   matters most:
   > *"Approving changes no files. Case-level work. Approving assigns and tracks
   > it; the system does not touch any files."*

   Show this on a case action, then scroll to the data-fix card and show the
   amber version:
   > *"Approving changes files. Data-quality fix 'normalise_status_values' is on
   > the machine-safe list — approving writes cleaned copies and leaves the
   > originals untouched."*

   > **Line to say:** "The previous build ran the spreadsheet cleaner whenever
   > *any* fix was approved. Approving 'chase the four overdue invoices' would
   > quietly rewrite status columns. Routing is now by action category, and
   > `is_machine_executable` is the single predicate that authorises a file
   > write." ([actions/execute.py](actions/execute.py))

8. **"Nothing here was sent to an AI"** / **"See exactly what was sent to the
   AI"** — **[COMPUTED]** from whether the item carries an `llm_payload`. Most
   queue items were never sent anywhere. Click it on a structural item →
   PayloadModal → the exact JSON, with `[PERSON_3]`, `[MASKED_1]` highlighted in
   green and a count. Caption: *"This is everything that left this machine."*

9. **Owner / Due date / note** — **[HUMAN]**. The system *suggests* an owner
   (`owner_by_category` in **[CONFIG]**: advisory case actions default to
   "Engagement lead") but never silently allocates someone's time.

10. **Approve & assign / Reject / Not relevant to us.**

#### What actually happens when you click Approve & assign

```
browser → POST /api/actions/advisory/{id}/decision
        → FastAPI shells:  python -m actions.cli decide ...
        → actions/execute.py::approve()
             ├─ create_intervention()   captures BASELINE from the current snapshot
             ├─ lifecycle.transition → approved → assigned (owner given)
             ├─ store.upsert_intervention → outputs/interventions_advisory.json
             └─ route(item) → "tracked" (nothing runs) or "machine" (remediate subprocess)
        → response carries an `execution` block
```

The green flash bar reads either *"Approved and tracked. No files were changed —
…"* or *"Approved. Cleaned copies written; your original files are untouched."*
**[UI]** text, driven by the real `execution.executed` boolean.

Then the progress buttons appear: **Assigned → In progress → Completed**.
**[HUMAN]**, one POST each. On completion:
> *"Marked done. It will be measured against the next analysis of your drive —
> nothing becomes trusted advice until then."*

#### Toggle: **"What we did about it"** (the intervention board)

**You click:** the second pill at the top of Today.

| Element | Source |
|---|---|
| **baseline 288600 ↓ now 249500 (−13.5%)** | **[COMPUTED]** — baseline captured at approval, observed measured from a *later* analysis snapshot ([actions/outcome.py](actions/outcome.py)) |
| *"projected at approval: 86580 — a forecast, not a result"* | **[CONFIG-ish]** `baseline × (1 − expected_improvement_pct/100)`, where `expected_improvement_pct` is a **hand-written guess per template** (delay 40%, repetition 50%, SLA breach 80%). It is displayed and **never** feeds the verdict. |
| The verdict colour + reason sentence | **[COMPUTED]**, tri-state. `MATERIAL_CHANGE = 0.10` is hardcoded: a move inside ±10% returns `effective = None` — *"too small to call either way"* |
| **"Measure against the latest analysis"** button | `POST /api/actions/{p}/review` → measures every *completed* intervention. Measuring work nobody has done yet "would be scoring the weather" |
| **"Confirm it worked" / "It did not work"** | **[HUMAN]**. The only route to `validated`, and therefore the only route into the retrievable resolution store. Overruling the system is allowed in both directions and is recorded as a disagreement. |

> **Line to say:** "Approval is not proof. An approved fix gets a baseline, an
> owner and a review date. It has to be finished, measured against a later
> snapshot of the same drive, and confirmed by a person before it becomes
> something the system will ever recommend again."

Confirming an effective outcome fires `pipeline.learn --promote` in its own
process → writes `data/learned/learned_resolutions_advisory.json` → embeds into
the `sme_resolutions` ChromaDB collection. Prove the gate holds by opening both
files side by side: `pending_*` has every approval ever (never embedded),
`learned_*` has only the validated ones.

---

### 4.4 Tab: **Mapping Review** — HITL Gate 1

**Use `joinery` for this.** It is the only profile whose committed proposal is a
real Claude output.

**You click:** the Mapping Review tab.

| Element | Source |
|---|---|
| *"Drive `…/messy_joinery` → profile `joinery`"* | file path |
| *"4 sheets scanned · **🤖 LLM audit** · claude-opus-4-8"* | **the tell.** `proposal.mode` is `"llm"` or `"offline"`. On foyle/advisory right now it reads *"📐 heuristic (offline) · none"* |
| **Mess report** (amber box) | **[CLAUDE]** — cross-file reasoning. e.g. *"jobs spring - Marks copy.xlsx appears to be a renamed personal copy potentially duplicating jobs 2026.xlsx rows…"*, *"No event coverage for 2025…"* |

> **The demo moment:** switch to a profile with an offline proposal and show the
> mess report is **empty**. The heuristic baseline literally cannot produce one —
> it does per-header alias lookup and has no cross-file view
> ([audit/propose.py:29](audit/propose.py#L29)). That gap *is* the argument for
> the LLM audit.

**You click:** a file row to expand it.

| Element | Source |
|---|---|
| **Role** dropdown (`events` / `reference` / `notes` / `ignore`) | pre-filled with **[CLAUDE]** `inferred_role` |
| **Ingest** checkbox | pre-filled with **[CLAUDE]** `include` — the model recommends `include=false` for stale duplicates *even when the role is `events`* |
| **role confidence 92%** (green/amber/red at 0.8/0.5) | **[CLAUDE]** when mode=llm. When offline it is a **flat hardcoded 0.4** for every file — say this, it is why the offline badge matters |
| Per-column `Job#  →  [case_id ▾]  87%` | **[CLAUDE]** per-column mapping + confidence. This is where the joinery story lives: the renamed-header fork (`Job#/Phase/When/Who`) that the alias baseline cannot resolve |
| *"🔒 Human-in-the-loop: nothing is ingested until the mapping is approved"* + *"● 3 corrections"* | **[COMPUTED]** live diff of your edits against the proposal |

**You click:** **✅ Approve mapping**.

```
POST /api/mapping-approvals
  ├─ writes  bottleneck-ingest/mappings/approved_joinery.json    ← what ingest reads
  └─ appends hitl-react/api/mapping_decisions.jsonl              ← the HITL METRIC
       { action: "modify", corrections: {role_changes, column_changes,
         include_toggles}, time_to_decision_seconds }
then App.jsx calls activateProfile() → re-ingest + re-export
```

`time_to_decision_seconds` is measured in the browser from the moment the card
mounted. **Those are the measurable HITL numbers in the dissertation** — not an
estimate, a logged observation.

> ⚠️ **Do not click Approve on `foyle` during the demo** unless you mean to. It
> overwrites `mappings/approved_foyle.json` and moves the eval numbers. Recovery:
> `git checkout mappings/approved_foyle.json`.

**The eval table this screen produces** (`outputs/eval_mapping_*.json`), column
mapping **F1**, three conditions:

| Profile | baseline (alias heuristic) | LLM | human-approved |
|---|---|---|---|
| foyle | 0.846 | 0.968 | 1.000 *(currently 0.800 — drifted, see above)* |
| **joinery** | **0.308** | **0.909** | **1.000** |
| advisory | 0.500 | *not yet run online* | 1.000 |

Joinery is the headline: the baseline **collapses** on the renamed-header fork,
the LLM recovers it, the human gate closes the residual (2 column corrections).

---

### 4.5 Tab: **Workflow** (Operations Overview)

Three columns. This is **supporting evidence**, not the product — say that.

**Left — Cases.** One row per detected bottleneck from `ui_cases.json`.
> **Honesty flag:** the `12h` / `3d` age on the right of each row is
> `Date.now() − detected_at`, i.e. **time since the export ran**, not since the
> business problem began. On a fresh run everything reads a few hours old.

**Middle — the workflow DAG** (`@xyflow/react`).

| Element | Source |
|---|---|
| The nodes | **[COMPUTED]** stages actually present in the log, ordered by **[CONFIG]** `stage_order` |
| Red/amber glow on a node + `● delay · 5 cases` | **[COMPUTED]** `detect_dynamic` output; when several bottlenecks share a stage the node shows the biggest |
| Edge thickness / animation / count label | **[COMPUTED]** real transition counts between stages |
| Node positions | **[UI]** client-side topological auto-layout — no meaning beyond readability |
| **Hover a node** → tooltip *"fed by 2 sheets: • proposals 2026.xlsx • delivery log.xlsx"* | **[COMPUTED]** from the file part of each `source_ref`. Nice payoff for the messy-drive story: it shows which spreadsheet feeds which stage |

**Right — Aging & Insights.**

| Element | Source |
|---|---|
| Outstanding / Avg age / Oldest | **[COMPUTED]**, but again against `detected_at` — same caveat as the case list |
| The `<7 / 7–14 / 14–21 / >21 days` bars | bucket edges **hardcoded** in [AgingQueue.jsx:3](../hitl-react/src/components/dashboard/AgingQueue.jsx#L3); on a fresh export they all pile into one bucket |
| **"Cases clean" donut** | **[COMPUTED]** `1 − cases_affected / cases` from the KPI block |
| "Needs Attention" rows | first 4 bottleneck cases, **[COMPUTED]** |

**Bottom — Impact over time (sparklines).**

- Four series: estimated cost exposure, cases affected, open bottlenecks, messy
  status cells. Each point is **one real pipeline run**, read from
  `outputs/history_<profile>.jsonl` — appended by `export_messy` and by a
  remediation apply. Fewer than two runs → it says *"needs 2+ runs"* rather than
  drawing a fake line.
- The dashed **"Projection"** strip at the bottom is explicitly labelled and
  ends with *"Arithmetic on the cost model, not a forecast."* **[CONFIG ×
  COMPUTED]**.

---

### 4.6 Tab: **Bottlenecks**

Two-column card grid over the same `ui_cases.json`. This is where you show what
the AI wrote — **if** it was run online.

| Element | Source |
|---|---|
| Card title + description | **[CLAUDE]** `"{diagnosis} Root cause: {root_cause}"` when online; **[TEMPLATE]** `_TYPE_FALLBACKS` when offline (*"Cases stall entering Proposal — 5 cases waited an average of 14 days…"*) |
| **HIGH / MEDIUM / LOW** badge | **[COMPUTED]** with hardcoded thresholds in [export_messy.py:106](bridge/export_messy.py#L106): delay is high if ≥25% of cases or the metric ≥12 days |
| `📊 avg_delay_days = 14.0` | **[COMPUTED]** detector metric |
| `💰 ≈ £8,400 · 5 cases × 14 days × £120/day` | **[CONFIG × COMPUTED]** — the cost model with its basis spelled out |
| **"🧠 Diagnosis grounded in 3 past resolutions"** (expandable) | **[COMPUTED]** ChromaDB vector retrieval. `similarity_score = 1 − L2distance/2` over normalised sentence-transformer embeddings ([diagnose.py:116](pipeline/diagnose.py#L116)) |
| Purple **"Learned"** badge on a retrieved row | that resolution came from **this system's own validated history**, not the seeded corpus |
| **"AI confidence 84%"** bar | **[CLAUDE]** `result.confidence` when online. **When offline it is a flat hardcoded `0.8` for every card** ([export_messy.py:192](bridge/export_messy.py#L192)) — volunteer this |
| Purple **"🤖 AI-SPOTTED · UNVERIFIED"** card, no confidence bar, *"advisory — no confidence stated"* | **[LOCAL]** Ollama. Payload is **stage names and aggregate numbers only** — no cell values, no case ids, no actors, no status strings. There is a test asserting exactly that. Never evaluated against ground truth, never gates anything |
| **"🔍 what the AI saw"** | PayloadModal again — and note the payload is built **even offline**, so you can show it without spending an API call |

**The detection eval this screen sits on** (`outputs/eval_detection_*.json`),
macro-F1 against seeded ground truth:

| Profile | marker baseline | dynamic detector |
|---|---|---|
| foyle | 0.524 | **1.000** |
| joinery | 0.523 | **1.000** |
| advisory | 0.471 | **1.000** |

The baseline scores **precision 0.10–0.17** on repetition and rework — it flags
every case that merely *contains* the marker stage. The dynamic detector asks
whether the stage genuinely repeats without progress, or is genuinely returned
to. Same recall, 6× the precision.

> **Circularity guard, say it before you are asked:** the generator
> (`synthetic/generate_messy_*.py`) and the detector (`detection/dynamic.py`) are
> cleanly separated. The detector has no access to the injection rules — it
> derives its own outlier threshold from the log's own gap distribution
> (Q3 + 1.5×IQR).

---

### 4.7 Tab: **Fixes & Cleanup**

**Top strip — HITL metrics.** Gate 1 and Gate 2 counters: mappings, corrections,
approved/modified/rejected, average seconds per decision. **[COMPUTED]** from the
same decision records that are appended to `mapping_decisions.jsonl` /
`decisions.jsonl` — it is a window onto the evaluation log, not a separate
counter. Session-scoped (localStorage), so it resets on a hard refresh.

**Middle — AI Data Cleanup (the remediation executor).**

**You click:** nothing; it loads on mount via `GET /api/remediation/{p}`, and
auto-regenerates if the event log or approved mapping is newer than the plan.

| Element | Source |
|---|---|
| *"3 files · 200 cells to normalise · **📐 rule-based map**"* | the mode badge. **The API never passes `--llm`**, so in the browser this is *always* rule-based — a deterministic keyword list in [remediate/propose.py:24](remediate/propose.py#L24). Say so; do not let the "AI Data Cleanup" heading imply otherwise. To show the Claude version, run `python -m remediate.run --profile advisory --llm` in a terminal |
| Value-map table: `"done ✔" → Complete ×34 90%` | **[COMPUTED]** rule output; the confidences (0.9 done/na, 0.6 freetext, 0.5 blank) are hardcoded to mark the guessy cases for the reviewer |
| *"🔒 review the value map above, then approve"* | Gate 2 for this action |

**You click:** **✅ Approve & clean 200 cells** → `POST .../apply` → writes
cleaned copies to `data/synthetic/messy_advisory_cleaned/` and returns a
before→after diff table. **Originals are never touched** — show the folder.

**Bottom — Process fixes (one HITLCard per bottleneck).**

| Element | Source |
|---|---|
| **"AI Suggestion"** badge + confidence | same source as the Bottlenecks card |
| **Editable steps textarea** | pre-filled with **[CLAUDE]** or **[TEMPLATE]** steps. Edit one → an amber *"● modified"* appears and the recorded action becomes `modify` instead of `approve`. That distinction is a dissertation metric |
| **Retrieved context** table (source / excerpt / match %) | **[COMPUTED]** ChromaDB, with the purple *Learned* badge where applicable |
| **✅ Approve & record / ✖ Reject** | `POST /api/decisions` → appends `decisions.jsonl` → fires `pipeline.learn --decision-json` in its own process → writes **only** to `pending_resolutions_<p>.json`. **Never embedded.** |
| The green confirmation card afterwards | shows `⏱ human review took 4.7s` — the logged HITL timing |

---

## 5. The full demo-day click order

Twelve minutes, no dead air.

1. **Header → switch to Northstar Advisory.** Let the overlay land. Deliver the
   generalisability line over the two-column KPI comparison.
2. **Today → the impact strip.** Four numbers. Name which are measured and which
   are projected *as you read them*.
3. **Expand the top card** ("16 engagements worth £288,600 not yet billed").
   Walk: evidence → spreadsheet row references → recommended action → impact
   basis in words.
4. **Point at the execution notice.** *"Approving changes no files."* Scroll to
   the data-fix card and show the amber *"Approving changes files."* Deliver the
   routing-bug line.
5. **Click "Nothing here was sent to an AI"**, then find a structural card and
   click **"See exactly what was sent to the AI."** Highlighted placeholders.
   *"This is everything that left this machine."*
6. **Approve one item** with an owner and a due date. Read the flash message.
   Walk it Assigned → In progress → Completed.
7. **Toggle to "What we did about it."** Baseline vs projected vs now, side by
   side, labelled differently. Deliver the approval-is-not-proof line.
8. **Mapping Review, on joinery.** The 🤖 badge, the mess report, the per-column
   confidences. Then flip to foyle and show the 📐 badge with an empty mess
   report — the baseline→LLM gap, visible rather than asserted.
9. **Bottlenecks.** Expand *"Diagnosis grounded in 3 past resolutions."* Point at
   a **Learned** badge if one is present. Show a purple AI-SPOTTED card and say
   plainly that it is unverified and runs on a local model at zero cost.
10. **Fixes & Cleanup.** The value map, then **Approve & clean** → the
    before→after diff. Open `messy_advisory_cleaned/` next to the original folder.
11. **Terminal.** `python -m pipeline.agent --profile foyle --offline`, then open
    the `agent_run_*.json`. *"That is the same pipeline core as a LangGraph
    state machine, with Gate 2 as a conditional edge."*
12. **Terminal.** `python -m eval.score_detection --profile advisory` and
    `python -m eval.score_mapping`. Land on the two tables.

---

## 6. Caveats to volunteer before you are asked

Volunteering these reads as rigour. Being caught on them reads as the opposite.

1. **The dashboard does not run the LangGraph loop.** FastAPI orchestrates the
   same steps as CLI subprocesses. The graph is a second, auditable entry point.
2. **`advisory`'s mapping proposal was generated offline**, so its baseline and
   "LLM" eval conditions are the same heuristic. `python -m audit.run --profile
   advisory` (online, one API call) fills the middle column.
3. **`mappings/approved_foyle.json` currently scores 0.800, not 1.000** — it was
   re-approved in the browser. That is the documented mapping-drift hazard, and
   it is itself a finding about HITL systems. `git checkout` restores it.
4. **`detection_confidence` is a hand-set calibration constant per finding
   type**, not a learned probability.
5. **`expected_improvement_pct` is an authored guess per template.** It is
   displayed as a projection and never touches the effectiveness verdict.
6. **The Fixes tab's cleanup is rule-based in the browser**, not LLM, because the
   API never passes `--llm`.
7. **The longitudinal replay uses a simulated oracle Gate-2 approver**, not a
   human. And the stream is a *recording*, not a counterfactual — an intervention
   approved at tick *t* cannot change what tick *t+1* contains, so a validated
   outcome there evidences the **measurement machinery**, not causation.
8. **All data is synthetic.** No live Foyle credentials, no crawler, no
   deployment. Real Foyle data would only ever be a one-off, consented,
   supervisor-signed-off export.
9. **The `ANTHROPIC_API_KEY` was pasted in a dev chat and must be rotated** at
   the Anthropic console before submission.

---

## 7. Likely questions, with the file to open

| Question | Answer | Open this |
|---|---|---|
| "How much of this is actually AI?" | Four call sites — three Claude, one local Ollama. Detection, ranking, impact and the outcome verdict are deterministic code. That is a design claim: a worker can argue with arithmetic. | §2 above |
| "Isn't the bottleneck count just configured?" | No. `detect_dynamic` returns 0..N findings from a statistical scan of every stage; the threshold is the log's own Q3+1.5×IQR. `markers` in config are **eval-only**, used to score the baseline it beats. | [detection/dynamic.py](detection/dynamic.py) |
| "How do you know detection isn't circular?" | Generator and detector are separate modules; the detector never reads the injection rules. The advisory generator records only *where it parked* each engagement — the rules decide independently whether that breaches an SLA, and flag strictly fewer than were parked. There is a test asserting that. | [tests/test_case_rules.py](tests/test_case_rules.py) |
| "What stops it emailing a client by mistake?" | Nothing is executed except one template on `MACHINE_EXECUTABLE_TEMPLATES`, and even that writes copies. `ActionItem.is_machine_executable` is the single predicate; nothing else may authorise a write. | [actions/execute.py:46](actions/execute.py#L46) |
| "What PII goes to Anthropic?" | None. spaCy NER + regex scrub every cell first; the mapping payload is headers plus 5 scrubbed sample rows. There is a test asserting placeholders only. And the dashboard renders the exact payload. | [scrub/anonymise.py](scrub/anonymise.py), the PayloadModal |
| "How is generalisability more than a claim?" | Three contrasting SMEs — educational tourism, joinery, professional services — through one core. SME #2 and #3 each added a config block, a synthetic drive and an approved mapping. Zero new reader, detector, action or ranking code. Switch profile live and show it. | [config.py:144](config.py#L144) |
| "Does the system learn?" | Yes, but only from proven fixes. Approval writes to `pending_*` (never embedded). Only an intervention measured against a *later* analysis and confirmed by a human reaches `learned_*` and the vector store. A one-off migration already demoted 3 foyle entries written under the old approval-is-proof rule. | [pipeline/learn.py](pipeline/learn.py) |
| "What if the fix doesn't work?" | `effective` is tri-state. A move inside ±10% returns `None` — "not enough evidence yet" — which is the honest answer most often. `None` never becomes trusted knowledge and is never written off as failure either. | [actions/outcome.py](actions/outcome.py) |
| "Why not pyarrow / why fastparquet?" | Importing pyarrow loads the Arrow C++ runtime, which segfaults in-process with chroma/hnswlib + torch on Windows. Same reason `audit/` and `remediate/` run as separate processes. | [requirements.txt:7](requirements.txt#L7) |
| "How many tests?" | 193 passing. `python -m pytest -q`. | — |

---

## 8. If something breaks live

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard shows correct data, then stale data, no pattern | **Two uvicorn instances on :8000.** `SO_REUSEADDR` lets the second bind, and Windows hands each connection to either one | `Get-CimInstance Win32_Process -Filter "Name='python.exe'" \| Where-Object { $_.CommandLine -match 'uvicorn\|multiprocessing-fork' } \| Stop-Process -Force` then restart one |
| Today tab says "Analysing this workspace…" for a minute | A profile switch re-ingests. Expected; the copy says so | Wait. Timeout is 900 s and returns a 504 with the command in it |
| One SME's branding over another's findings | The event log is one global file | `bridge.export_actions` refuses a mismatched log; the API re-ingests first. If it slips, re-run `ingest.py --source messy --profile <p>` |
| `UnicodeEncodeError` in a terminal | Status values contain `✔`; cp1252 can't encode it | `$env:PYTHONIOENCODING="utf-8"` |
| `curl http://127.0.0.1:5173` fails | Vite binds IPv6 | Use `http://localhost:5173` |
| Eval numbers moved unexpectedly | You approved a mapping in the browser | `git checkout mappings/approved_*.json` |
| A bare `python` command hits the Windows Store | Shim | Always `.venv/Scripts/python.exe` |
