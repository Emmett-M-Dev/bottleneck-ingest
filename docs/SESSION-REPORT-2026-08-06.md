# Session report — 2026-08-05/06

Written for the next assistant session. Read `CLAUDE.md` first; this covers what
changed in this session and what it cost, including several things that were
believed true and turned out not to be.

**Two repos.** `bottleneck-ingest` (this one) and `../hitl-react` — the dashboard
is a **sibling directory, not a subdirectory**. `api/main.py` resolves the
pipeline as `_HERE.parent.parent / "bottleneck-ingest"`, so that relationship is
load-bearing. A previous session concluded the dashboard was lost because it
looked for it inside this repo.

| Repo | Final commit | State |
|---|---|---|
| `bottleneck-ingest` | `8893e75` on master | 281 tests passing |
| `../hitl-react` | `ce99289` on master | production build succeeds |

---

## What was built

### 1. The live SME simulator (P1) — merged earlier in the session

`simulator/` — a world that advances one day at a time and renders the messy
drive the product ingests. The problem it solves: all three previous data paths
were fixtures, so an approved fix could never change what the next analysis saw.
`lifecycle.validated` was pinned at 0 across the replay window not because the
mechanism was broken but because affected-case counts can only grow in a
recording.

Nine modules: `world`, `step`, `intents`, `personas`(folded into profiles),
`compose`, `worker`, `render`, `profiles`, `cli`. The interface to the product is
**the drive on disk and nothing else** — `ingest.py --drive` already existed, so
the product needed no changes.

Run it:

```bash
.venv/Scripts/python.exe -m simulator.cli --profile advisory --reset
.venv/Scripts/python.exe -m simulator.cli --profile advisory --advance 7
```

Design points that matter for the write-up:
- Effects are **probabilistic below 1.0** — some approved actions fail. A
  simulator where every approved fix works is authored to flatter the product.
- The deterministic sim decides **what** happens; an LLM fills only names and
  figures, cached per (seed, day, message) so replays are free and offline.
- `simulator/render.py` still hard-codes advisory filenames, so the
  "SME #2 is a config block and zero engine code" claim is **not yet fully true
  for the simulator**. Do not overclaim it.

### 2. Demo mode in the dashboard (P3)

A **Demo** tab driving the simulator. Day counter advances, client mail drips
into a sidebar and strikes through when the simulated staff apply it, changed
rows flash in a live sheet grid, and at each sim-week boundary the product
re-ingests the simulated drive and the vitals move.

`POST /api/sim/{profile}/analyse` is the one place the dashboard deliberately
points ingest at the simulated drive.

The day counter **deliberately stalls ~50s** at each boundary while that runs. A
counter that kept ticking would be racing ahead of the data it claims to reflect.

### 3. The dashboard simplification

Five tabs → **three**: Today, Mapping Review, Demo. `CLAUDE.md` §1 already said
charts and aggregate summaries are "supporting evidence for that queue, not the
product", and the UI was giving them equal billing.

### 4. Codebase simplification (~2,100 lines removed)

`ingest.py` went from 8 sources to 2 (`messy`, `local`). Removed four readers,
two generators, three exporters, and the local-LLM anomaly pass. **Every
published number was re-verified afterwards and none moved.**

---

## Things that were believed true and were not

These are the ones worth knowing about, because they were all caught by review
rather than by testing, and several were wrong in a way that looked fine.

1. **The demo's central beat did nothing.** The week boundary called
   `reloadQueue({rebuild:true})`, which only recomputes from the existing
   parquet. `get_actions` re-ingests only when the event-log profile differs —
   which never happens mid-demo. So the product never re-read the simulated
   drive and the vitals were static by construction. Found by watching the
   ImpactStrip not change, not by reading code.

2. **Simulated data contaminated the tracked evaluation artefacts.** The
   committed advisory record is **25 items**; the working tree had **32**, seven
   created on simulated days including one affecting `NA-1069`, a case in no
   static drive. `merge_actions` deliberately keeps items whose finding stopped
   recurring, so re-ingesting can never remove them. An earlier "restore
   verified" claim in this session was **wrong** — it checked the event log, not
   the action store.

3. **The RAG grounding was nearly deleted.** `BottleneckCard` rendered
   `retrieved_resolutions` and the fold was going to delete it. Moved onto the
   action card first, and Task 12 was hard-gated on visually confirming it.
   (Note: `HITLCard.jsx` also renders it, so "the only place" was itself wrong.)

4. **`retrieved_resolutions` is only populated when the diagnosis runs online.**
   It is initialised `[]` and filled inside `if not offline:`. An offline export
   produces grounding-free data that looks identical in shape.

5. **A `bn.id` join hazard `CLAUDE.md` called "known and unfixed" is now fixed** —
   `detection/detect.py::finding_key`. But the fix initially targeted the wrong
   exporter (`export_cases.py`, the legacy path) rather than `export_messy.py`,
   the live one.

---

## Outstanding — do these before demoing

**1. foyle and joinery need a re-ingest.** Provenance tracking now fails closed:
a snapshot written before `source_drive` existed counts as unknown origin and is
refused, so a simulated week can never become the baseline or the observation for
a real intervention. Every foyle and joinery snapshot on disk predates the field,
so their approve/review buttons currently refuse.

```bash
.venv/Scripts/python.exe ingest.py --source messy --profile foyle
.venv/Scripts/python.exe -m actions.cli queue --profile foyle
# repeat for joinery. advisory is already done.
```

**2. Do not run two dashboards at once.** There is no lock on
`outputs/event_log.parquet`. The demo serialises its own analyse call, but a
profile switch or a second tab during an in-flight analyse puts two `ingest.py`
processes on the same bare `df.to_parquet`.

**3. After any demo, restore the static drive** before re-running an evaluation:

```bash
.venv/Scripts/python.exe ingest.py --source messy --profile advisory
```

The Today tab shows an amber strip while the queue was built from an alternate
drive, so this is detectable rather than silent.

---

## Write-up consequences

Three claims the dissertation currently makes that are no longer true:

- **Impact-history sparklines are gone.** Deleted with the Workflow tab, no
  replacement. `GET /api/history` and `outputs/history_<p>.jsonl` still exist so
  it is restorable, but nothing renders it. Do not claim a trend view.
- **There are three LLM call sites, not four.** The Ollama anomaly pass was
  removed. Write Ollama up as a **development finding**: it was trialled and
  withdrawn on resource grounds, which supports §3's resource-constraint
  argument. Past tense, and it is **not** a privacy control of the delivered
  system — §9 has one implemented control, the zero-PII scrub, not two.
  [YOU] That finding needs a number beside it (machine spec, model, rough
  latency) or a viva panel will call it an anecdote.
- **The demo proves the measurement machinery, not causation.** The world's
  responsiveness is authored — `effect_prob` is a config constant. What it
  demonstrates is that an improvement can now be *observed*, which the pre-baked
  stream structurally could not.

Also still outstanding from before this session: §7's replay curves predate the
simulator, and P2 (retargeting `eval/replay.py` onto the simulator) is not
started. P2 is where `lifecycle.validated` finally gets a chance to move off 0.

---

## Method note

Both P1 and P3 were built with subagent-driven development: implement → review →
fix → re-review, per task. Nearly every defect above was found by a reviewer
rather than by a test, and several were found only because a reviewer ran the
thing and watched what happened instead of reading the code. If you continue this
work, the single highest-value habit to keep is: **make the implementer verify by
observation, and make the reviewer try to break the claim.**
