# Closing the P0 eval debt — design

_Date: 2026-07-31. Status: approved, pre-implementation._

## Problem

Four TASKLIST Priority-0 items are open, and every one of them is a number the
dissertation has to defend at viva:

1. `eval/replay.py` became outcome-gated on 2026-07-23, but the committed replay
   artefacts in `outputs/` are dated 19/07 — they were produced by the old
   approval-gated loop. The cited learning curve describes code that no longer
   exists.
2. `advisory`'s mapping proposal was generated `--offline`, so its baseline and
   "LLM" conditions are the same heuristic (both 0.500). The headline F1 table
   has a hole in it.
3. `mappings/approved_foyle.json` drifted during a browser re-approval and now
   scores human F1 0.800 rather than the documented 1.000.
4. `foyle` and `joinery` drives were seeded for structural bottlenecks only —
   every case reaches a terminal stage — so their Today action queues are empty.
   The action layer, which is the product, is demonstrated on one SME of three.

Two facts discovered while scoping, which change what the fix is:

- **The foyle mapping drift is already committed.** `git status` is clean on
  `mappings/`; the drifted file went in with `c92caef`. `git checkout
  mappings/approved_foyle.json` restores the drift, not the good mapping. The
  real restore is `git checkout a8e3437 -- mappings/approved_foyle.json`.
  Docs currently give the wrong command.
- **foyle's on-disk mapping eval is also offline.** `outputs/eval_mapping_foyle.json`
  carries `"model": "none"`, so its baseline and LLM columns are identical there
  too. The cited 0.968 exists only in `eval/results/`. Two of three profiles'
  LLM numbers are not reproducible from `outputs/`.

## Approach

Phased, so the cheap independent fixes are banked and committed before the
generators are touched. Re-seeding invalidates ground truth *and* the stream
drives that feed the replay, so seeding must happen before the replay re-run or
the replay runs twice.

Rejected alternatives:

- **Single sequenced pass** (seed first, everything else after, one commit). Same
  work, no checkpoint. If seeding moves detection macro-F1 off 1.000, three docs
  are being rewritten mid-flight with nothing banked.
- **Maximum fidelity** (`eval.replay --llm`, online audits for all three
  profiles). Multiplies API cost and replay runtime. Offline replay is the *more*
  defensible choice for a cited curve: it is reproducible.

## Phase 1 — mapping table (no drive churn)

1. `git checkout a8e3437 -- mappings/approved_foyle.json`, then
   `.venv/Scripts/python.exe -m eval.score_mapping --profile foyle`.
   Expected: human F1 returns to 1.000. Commit the mapping so it cannot drift
   silently again.
2. `.venv/Scripts/python.exe -m audit.run --profile advisory` **online** (one
   Claude API call), then re-score advisory. Fills the LLM cell.
   **Do not re-approve in the browser.** `score_mapping`'s `approved` condition
   reads `mappings/approved_advisory.json`, which currently scores 1.000; a
   browser approval would overwrite it and reintroduce exactly the drift being
   fixed in step 1.
3. Same online audit for `foyle` — in scope, one further API call — so `outputs/`
   stops claiming `"model": "none"` beneath a cited 0.968. Expected LLM F1
   ≈ 0.968; whatever comes out is what gets cited. Two API calls total for the
   whole phase.
4. Update the F1 tables (CLAUDE.md §7, HANDOVER §6) and the wrong `git checkout`
   command in CLAUDE.md §10 / TASKLIST. Commit.

## Phase 2 — operational seeding

### What changes

`synthetic/generate_messy_foyle.py` and `synthetic/generate_messy_joinery.py`
gain the operational-pattern mechanism that `generate_messy_advisory.py` already
proves out:

- `_case_events` / `_job_events` gain `park_at: str | None = None` and
  `unowned: bool = False`. Park = stop emitting events at that stage. Unowned =
  blank actor cell for that case's rows.
- A per-generator plan table lists the extra operational cases: which stage each
  parks at, how many weeks stale, whether it is unowned. Case ids continue each
  generator's existing scheme (foyle: `B-2026-019` onward).
- An `AS_OF` constant plus a post-build shift so the newest event in the drive
  lands exactly on `AS_OF`, as advisory does. Staleness becomes deterministic
  rather than dependent on when the generator was last run — `detect_case_findings`
  defaults `as_of` to the newest event in the log, so this is what makes the
  seeded staleness reproducible.
- The ground-truth JSON gains `operational_intent: {parked_at: {stage: [cases]},
  unowned: [cases]}` — the same key names advisory writes, so the tests generalise
  instead of being duplicated.

Existing structural cases keep their exact flags and start dates. The generator
records only *where it parked* each case; the rules decide independently whether
that is a breach. That separation is the circularity guard and it is asserted by
test, not by comment.

### Hard constraints on the seeding

- **No new columns and no new files.** Foyle stays at exactly 5 files
  (`test_messy_ground_truth` asserts the file list) and neither drive gains a
  column, so the mapping ground truth — and therefore the Phase-1 F1 table —
  is untouched by Phase 2. Unowned is a *blank* `Handled By` / `Fitter` cell,
  not a new owner column.
- **Pre-park gaps come from each generator's existing 1–3 day distribution**, so
  the log's own Q3+1.5×IQR delay threshold barely moves. Parked cases stop
  emitting rather than emitting a long gap, so they add no delay signal.
- **Five rules, not six.** Neither drive has a money column, so
  `unrealised_value` cannot fire for foyle or joinery. This is stated in the
  write-up as a property of those SMEs' data, not worked around by inventing a
  value column (which would break the constraint above).

### Tests

- Generalise the three advisory-only case-rule tests to
  `@pytest.mark.parametrize("profile", PROFILES)`:
  flagged ⊆ parked; flagged non-empty; flagged **strictly** ⊂ parked; unowned
  findings match the seeded intent exactly.
- New test: profiles without a value column produce no `unrealised_value`
  finding — so the gap reads as a decision rather than a silent failure.
- `test_messy_ground_truth` contracts are unchanged; they regenerate consistently
  because the generator writes the ground truth it seeded.

### Re-run order

Nothing here is optional and the order matters — each step consumes the previous
step's artefacts:

```
regenerate foyle + joinery drives      (synthetic/generate_messy_*.py)
pytest -q                              (ground-truth + case-rule contracts)
eval.score_detection  --profile foyle | joinery
synthetic/generate_stream.py --profile foyle | joinery      (imports the generators)
eval.replay      --profile foyle | joinery                  (offline, --fresh default)
eval.plot_replay --profile foyle | joinery
bridge.export_actions --profile foyle | joinery             (queues should be non-empty)
```

## What gets re-cited

`CLAUDE.md` §7 (both tables) and §11, `HANDOVER.md` §6 and §7, `TASKLIST.md`
(P0 items 1–4), `PRESENTATION_WALKTHROUGH.md` numbers.

Honesty rules, agreed up front so there is no temptation later:

- Detection macro-F1 is cited as it lands. If seeding drops it below 1.000, that
  is the number, and the reason (a moved outlier threshold) is explained rather
  than tuned away.
- The replay is cited on **both** curves: `lifecycle.validated` (what the
  outcome-gated loop trusts) and `lifecycle.approved_unmeasured` (what the old
  approval-gated loop would have trusted by the same tick). The learning curve
  is expected to shift right and may sit lower. That is the result.
- Replay stays offline. The oracle Gate-2 approver is stated as an oracle, not a
  human, wherever the curve appears.

## Risks

| Risk | Handling |
|---|---|
| Seeding moves dynamic detection macro-F1 off 1.000 | Accepted and cited. Mitigated by drawing pre-park gaps from the existing distribution. |
| Mapping F1 table disturbed by Phase 2 | Structurally prevented: no column or file changes. |
| Browser re-approval re-drifts a mapping mid-work | No approvals during this work; approved mappings committed at the end of Phase 1. |
| Replay runtime is the long pole | Run per profile, offline; plots regenerate from the tick JSONL without re-running the loop. |

## Out of scope

Anything touching the live-product boundary (real drive crawls, credentials),
new detection capability, dashboard layout work, and the Phase 2–5 build reports.
This spec closes eval debt only.
