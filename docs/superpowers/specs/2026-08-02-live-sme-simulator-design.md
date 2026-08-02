# The live SME simulator — design

_Date: 2026-08-02. Status: awaiting approval, pre-implementation._

## Problem

The artifact has three data paths and all three are fixtures. None of them
simulate a business.

| Path | What it is | Fatal property |
|---|---|---|
| `data/synthetic/messy_<p>/` | one snapshot, hand-authored `_PLAN` | a photograph; time does not exist |
| `data/synthetic/stream_<p>/tick_NN/` | 9 cumulative snapshots, pre-baked | tick 3 exists before tick 2 is touched |
| `data/synthetic/messy_advisory_followup/` | `FOLLOW_UP_MOVES`, a hardcoded dict | world moves whether or not a human approved anything |

`FOLLOW_UP_MOVES` (`synthetic/generate_messy_advisory.py:259-271`) is the clearest
statement of the problem. It says engagement 11 advances to Qualification, always.
Approve every item in the queue or reject every one — the follow-up drive is
byte-identical. Outcome measurement therefore measures a decision it never
consulted.

The consequence is already recorded in CLAUDE.md §7 as an honesty note:
`lifecycle.validated` stays at **0** across the full 9-tick replay window, in both
profiles, while `lifecycle.approved_unmeasured` reaches 3. The cause was traced to
source: affected-case counts only *grow* as more of a recording is revealed, so
`actions/outcome.py::compare` can never return a measured improvement. That is not
a tuning bug. `tests/test_replay.py` proves the validation path works when a
finding genuinely disappears; the recording simply cannot produce the
counterfactual that a measured improvement requires.

Stated plainly: **the product's own loop — evidence → constraint → action →
measured outcome — is open at the last link, and the data architecture is what
holds it open.** The write-up currently absorbs this as a concession
(`eval/replay.py:34-38`, "the stream is a RECORDING, not a counterfactual").

Root cause, one line: world state is authored, not simulated, so interventions
have no write path into the world.

## Approach

Split the project in two.

```
┌─────────────────── SYSTEM 2: WORLD SIM (new) ────────────────────┐
│  clock (day tick)                                                 │
│      │                                                            │
│      ▼                                                            │
│  world state  ──┬──► inbound events ──► EMAIL COMPOSER ──► inbox  │
│  (cases,        │    "10 students",     (template + LLM   sidebar │
│   stages,       │    "can we move        variable fill)      │    │
│   owners,       │     the times?")           │               │    │
│   money)        │                            ▼               │    │
│      ▲          │                       WORKER SIM ◄─── approved  │
│      │          └───────────────────────────┤        ActionItems  │
│      │                                      ▼                     │
│      └────────────────── renders data/sim/<p>/drive/*.xlsx        │
└───────────────────────────┬───────────────────────────────────────┘
                            │  the drive on disk = the only interface
┌───────────────────────────▼───────────────────────────────────────┐
│  SYSTEM 1: THE PRODUCT (unchanged)                                │
│  ingest → Gate 1 → detect → RAG diagnose → action queue → Gate 2  │
└───────────────────────────────────────────────────────────────────┘
```

The simulator plays both sides of the SME's correspondence: the **clients** who
send work in, and the **staff** who type it into spreadsheets. The product is
untouched and unaware.

Rejected alternatives:

- **Branching pre-baked recordings** (generate an intervention arm and a control
  arm per tick, switch branch on approval). Cheaper, and it does produce a
  counterfactual, but it is combinatorially capped — a handful of pre-authored
  branches is not a business, and the "authored to flatter the product"
  criticism lands harder on a branch table than on a probabilistic simulator.
- **Real-time autoplay daemon.** Most alive-feeling, but tick-commit was chosen
  precisely to keep reproducibility, and a daemon adds process management plus a
  genuine race against ingest reading a workbook mid-write.
- **Simulator replaces all fixtures** (static drives re-rendered from sim day 0).
  Cleanest end state, single source of truth. Rejected on risk: it forces a third
  reseed of mapping F1 (3 profiles), detection F1 (3 profiles), action queues and
  case exports, all of which were re-cited days ago.

## Decisions

Settled during brainstorming, recorded so implementation does not relitigate them:

1. **Purpose is both** — a demo that feels live *and* an eval whose outcome gate
   can actually fire.
2. **Approvals feed back, curated subset.** A wired shortlist of finding types has
   modelled effects; everything else is inert and logged as unwired.
3. **Clock is hybrid.** The sim *day* is the unit of truth and commits atomically.
   Demo mode drips the day's messages into the UI on a timer; state and the
   spreadsheet write happen once, at the day boundary.
4. **Day 0 is the existing world.** The sim imports `build_events()` output
   directly. Static drives are frozen; `stream_<p>/` and the follow-up drive are
   retired.
5. **Emails are template skeletons with LLM-filled variables** (names, figures),
   cached to disk, with deterministic template fallback. Messages stay basic.
6. **Generic engine, advisory only wired.** No SME vocabulary in engine code;
   foyle and joinery would be a config block and zero engine code.
7. **Sim runs as a shell-out CLI**, matching the existing thin-orchestrator
   convention. Demo and headless eval share one entry point.
8. **Analysis cadence is weekly** in demo mode, configurable.
9. **Ollama is dropped entirely** — see write-up debt below.

## Architecture

### The contract

The interface between the two systems is **the drive on disk**, plus one
read-only read of the action store.

```
SIM writes ──► data/sim/advisory/drive/*.xlsx ──► PRODUCT reads
SIM reads  ◄── approved ActionItems (read-only) ◄── PRODUCT writes
```

Product code changes required: **none**. `ingest.py:285` already accepts
`--drive`, so the product ingests the live drive today:

```
python ingest.py --source messy --profile advisory --drive data/sim/advisory/drive
```

The sim never imports detection or diagnosis; the product never imports the sim.
`rm -rf simulator/ data/sim/` returns the repo to current behaviour exactly.

Inherited hard constraint (CLAUDE.md §6): the sim must **never** import chromadb,
pyarrow or torch. Dependencies are pandas, openpyxl and anthropic only.

### Module layout

```
simulator/
  world.py     WorldState = {case_id: Case}; Case owns an append-only event list.
               day0_from_generator() takes build_events() output directly.
  step.py      advance(world, approved) -> DayResult
                 1. arrivals  new enquiries, seeded Poisson draw
                 2. inbound   client/supplier messages against live cases
                 3. worker    apply messages + approved actions to world
                 4. drift     untouched cases age; stall probabilities apply
                 5. render    project world -> xlsx (atomic swap)
  intents.py   generic inbound intent catalogue
  personas.py  sender personas
  compose.py   template skeleton + LLM variable fill + on-disk cache
  worker.py    the SME staff: message -> row edit; approved action -> effect
  render.py    world -> messy sheets, reusing _lead_frame / _project_frame
  profiles.py  per-profile config block (advisory wired)
  cli.py       --advance N | --reset | --status ; JSON to stdout
```

On disk, per profile:

- `data/sim/<p>/drive/*.xlsx` — the live drive the product ingests
- `data/sim/<p>/state.json` — world state, day counter, RNG state, intent record
- `data/sim/<p>/inbox.jsonl` — every message, with `applied` flag and `row_ref`
- `data/sim/<p>/cache/` — LLM variable fills keyed `seed|day|msg_id`

### Determinism and the circularity guard

RNG is seeded per `(run_seed, day)`. The same seed plus the same approval
decisions produces a byte-identical drive.

The LLM fills names and figures only. It never decides which case, which stage,
or whether anything breached. Ground truth therefore stays computable, and the
CLAUDE.md §7 circularity guard holds unchanged: the simulator records only its
*intent* (which case it parked where, which pattern it injected); the detector
judges independently from the data.

Rendering writes to `.tmp` then `os.replace`. This removes the mid-write race, so
a demo tick can fire while ingest is reading.

### The feedback channel

Keyed on **case-rule finding type**, not template id, so the engine stays
SME-agnostic:

| Approved finding | Worker effect | Observable in re-analysis |
|---|---|---|
| `stage_sla_breach` | chases; client replies with probability p | stage advances, breach clears |
| `stalled_case` | re-contacts; case moves or is closed | idle days reset |
| `unowned_case` | assigns an owner from capacity config | actor populated |
| `unrealised_value` | raises invoice; payment email follows | case reaches a terminal stage |
| `overloaded_owner` | routes new arrivals off that owner | load drops below limit |
| `key_person_dependency` | a second person shadows the specialist | share drops below threshold |

Structural findings (delay / repetition / rework, which become
`process_intervention` items) change a simulator **parameter** — the stall
probability at that stage — affecting cases from that tick forward and never
retroactively. A process fix does not repair history.

Two deliberate constraints:

- Effects are **probabilistic, p < 1**. Some approved actions fail. Without this
  the simulator is authored to flatter the product, which is exactly the
  criticism to pre-empt at viva. Each wired finding type carries its own success
  probability in the profile config block, not in engine code, so the numbers are
  inspectable and quotable in the write-up.

The `approved` argument to `advance()` has two sources and one shape: in demo
mode the CLI reads approved `ActionItem`s from the product's action store; in
headless eval `eval/replay.py` passes the oracle approver's decisions directly.
The simulator cannot tell the difference and has no branch for it.
- `normalise_status_values` stays **out of scope for the sim**. The remediation
  executor already owns it (CLAUDE.md §4a) and must not be double-handled.

Unwired finding types are inert and logged as unwired, so coverage can be stated
honestly in the write-up.

### Dashboard demo mode

A **▶ Demo** button in the hitl-react top bar starts an auto-advance loop.

```
┌ inbox sidebar ─────┐ ┌ sheet grid ──────────────┐ ┌ vitals ──┐
│ ● NEW 09:14        │ │ leads.xlsx │ projects... │ │ open  38 │
│   R. Hughes        │ │ ─────────────────────────│ │ overdue 6│
│   "10 students,    │ │ NA-1041 Lead    02/03 EM │ │ unowned 4│
│    March intake"   │ │ NA-1052 Qual    03/03 —  │ │          │
│ ✓ M. Doherty paid  │ │ NA-1052 Qual  ★04/03 JS │ │  flash   │
└────────────────────┘ └──────────────────────────┘ └──────────┘
        clock  Mon 04 Mar  ── spinning ──►
```

An email lands on the left, the worker applies it, the affected row flashes on
the right, and the sidebar item strikes through. The clock advances day by day.

Analysis cadence is **weekly**: the clock and the inbox run day by day (pure sim,
fast), and the product re-analyses at each sim-week boundary with a visible
"re-analysing" beat, after which vitals and the action queue update. Configurable
via `--analyse-every N`, default 7.

The vitals strip is computed by **the product**, never by the simulator. A
simulator that reports its own health is a demo-integrity hole. "Vitals" here
means figures the product's existing analysis already produces — open case count,
cases past SLA, unowned cases — read from the `detection/case_rules.py` output of
the most recent weekly analysis, not a new metric path.

### Eval rewire

`eval/replay.py` retargets from `stream_<p>/tick_NN` to the simulator. The oracle
Gate-2 approver is unchanged; the one difference is that **approvals feed back
into `step()`**.

That single arrow is what makes `lifecycle.validated` capable of firing. Both
existing curves survive — detection F1 against a moving truth, and
`lifecycle.validated` against `lifecycle.approved_unmeasured` — but the second
now separates on merit rather than being pinned at 0 by the data architecture. If
it fails to separate under the new design, that is a real finding rather than an
artefact, which is the entire point of the change.

Per-tick ground truth comes from the simulator's intent record in `state.json`
rather than a pre-baked `ground_truth_stream_*.json`.

One honesty note survives and must stay in the write-up: **the tick-by-tick
approver is an oracle, not a human.** One honesty note is withdrawn: the
"recording, not a counterfactual" concession at `eval/replay.py:34-38`, because
the world now responds to interventions.

## Phasing

- **P1 — sim core.** `simulator/` package, CLI, headless advance, atomic render,
  feedback channel, determinism tests. Includes the `bn.id` fix (see Risk 1).
  Closes the loop with no UI at all.
- **P2 — eval rewire.** Retarget `eval/replay.py`, regenerate replay artefacts and
  curves, re-cite CLAUDE.md §7.
- **P3 — dashboard demo mode.** Demo button, clock, inbox sidebar, sheet grid,
  vitals wiring.

P2 before P3 because a dashboard demo can fall back to a static drive if time runs
out before submission; the eval claim cannot. Reversible if the demo is needed
sooner.

Each phase gets its own implementation plan. This spec covers all three so the
shape is agreed up front, but **only P1 goes to `writing-plans` next** — P2 and P3
are re-planned once P1 is built and its real cost is known.

## Out of scope

Unchanged from CLAUDE.md §2. The simulator generates **synthetic** correspondence
locally. No IMAP, no real mailbox, no live credentials, no deployed service. The
"emails" are rows in `inbox.jsonl` rendered in a local UI.

Also explicitly out: a control arm (same seed, interventions disabled) that would
support a genuine causal claim. It is mostly plumbing on top of P1 and is parked
as optional future work, not built.

## Risks

**1 — a known-unfixed hazard becomes a live bug.** CLAUDE.md §11 and HANDOVER §8
record that `actions/build.py::_structural_items` joins diagnosis prose onto
findings by `bn.id`, which `detection/dynamic.py` assigns by *rank order* rather
than content. The simulator reorders structural findings every tick by design, so
a latent mis-attribution becomes a guaranteed one. **This must be fixed inside
P1**, not deferred.

**2 — scope, with submission approaching.** Mitigated by the phasing above; P1
alone closes the loop for the write-up.

**3 — API cost.** Bounded by the cache. Fills are keyed `(seed, day, msg_id)`, so
a replayed demo costs nothing after its first run, and the template fallback
means no network is a degraded demo rather than a failed one.

## Write-up debt created

Dropping Ollama entirely removes the anomaly pass **feature**, not merely its
engine — the "AI-spotted" cards go away. The following need editing before
submission:

- **§4** architecture diagram — remove the local-LLM anomaly pass from the
  pipeline core box.
- **§6** tech stack — remove the Ollama row.
- **§6a** — the resolved "hybrid local/cloud division of labour" claim is
  withdrawn. LangGraph's half of that section stands.
- **§9** — the local-inference privacy control is withdrawn. The zero-PII scrub
  remains as the implemented control, and must not be described as one of two.
- **§7** — replay curves re-cited after P2; a new subsection describing the
  simulator as the data strategy for longitudinal evaluation.
- `HANDOVER.md`, `TASKLIST.md`, `DEMO.md`, `PRESENTATION_WALKTHROUGH.md` — run
  instructions for the retired paths.

Code to remove: `pipeline/llm.py` local path, `detection/anomaly.py`, their tests,
and the `OLLAMA_MODEL` / `OLLAMA_URL` environment handling.

## Retirement list

Removed once P2 lands:

- `synthetic/generate_stream.py`
- `data/synthetic/stream_foyle/`, `data/synthetic/stream_joinery/`
- `data/synthetic/ground_truth_stream_*.json`
- `data/synthetic/messy_advisory_followup/`
- `FOLLOW_UP_MOVES`, `FOLLOW_UP_WEEKS` and the `--follow-up` flag in
  `synthetic/generate_messy_advisory.py`

Frozen and untouched: all three static messy drives, the mapping F1 table, the
detection F1 table, the action queues and the case exports. No number in CLAUDE.md
§7 moves except the replay curves.
