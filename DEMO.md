# Demo walkthrough — the worker loop, on three SMEs

Everything below is copy-pasteable PowerShell from `c:\Users\Emmet\bottleneck-ingest`.
Call the venv python explicitly; bare `python` hits the Windows Store stub.

```powershell
$env:PYTHONIOENCODING = "utf-8"     # status values contain ✔, which cp1252 cannot encode
$PY = ".venv/Scripts/python.exe"
```

The story the demo tells:

> Analyse a workspace → get a prioritised action queue → assign and track the work
> → re-analyse a later snapshot → measure whether it worked → promote only the
> fixes that are proven into organisational knowledge.

---

## 0. One-time setup (only if the drives are missing)

```powershell
$PY synthetic/generate_messy_advisory.py
$PY synthetic/generate_messy_advisory.py --follow-up   # the same drive a fortnight later
$PY synthetic/generate_resolutions.py                  # the RAG corpus, all 3 profiles
$PY -m pipeline.embed_resolutions --profile advisory
```

`generate_messy_foyle.py` and `generate_messy_joinery.py` regenerate the other
two drives, but their outputs are committed — **do not re-run them unless you
intend to move the mapping-eval numbers.**

---

## 1. Northstar Advisory — the commercial demo

A fictional 16-person consultancy running lead-to-cash on spreadsheets.

```powershell
# Gate 1: what does the drive look like, and how should it map?
$PY -m audit.run --profile advisory --offline     # drop --offline to use Claude
#   → review + Approve in the dashboard's Mapping Review tab
#     (an approved mapping is already committed, so you can skip this)

$PY ingest.py --source messy --profile advisory
$PY -m bridge.export_messy --profile advisory --offline    # bottleneck evidence
$PY -m remediate.run --profile advisory                    # propose the data fix
$PY -m bridge.export_actions --profile advisory            # THE ACTION QUEUE
```

Expect roughly:

```
Northstar Advisory — Lead-to-cash (analysis date 2026-07-20)
14 open action(s) across 26 engagement(s)
  revenue at risk   £288,600      (measured — summed from the firm's own sheets)
  cost incurred     £10,826       (projected from the profile's cost model)
  by category       {'case_action': 7, 'process_intervention': 6, 'data_quality': 1}
  data confidence   97%
```

### 1a. Work the queue

```powershell
# Whatever id is at the top of the queue:
$AID = ($PY -c "from actions import store; print(store.load_actions('advisory')[0].action_id)")

$PY -m actions.cli decide   --profile advisory --action-id $AID --decision approve `
                            --owner "Niamh Foy" --due-date 2026-07-28
$PY -m actions.cli progress --profile advisory --action-id $AID --status in_progress
$PY -m actions.cli progress --profile advisory --action-id $AID --status completed
```

The `decide` response carries an `execution` block. For a case action it reads:

```json
"execution": {"executed": false, "mode": "tracked",
              "reason": "Case-level work. Approving assigns and tracks it; the system does not touch any files."}
```

**Approving the data-quality item is the only one that runs anything** — it
shells the remediation executor, which writes cleaned copies to
`data/synthetic/messy_advisory_cleaned/` and never touches the originals.

### 1b. Re-analyse a later week, and measure

```powershell
$PY ingest.py --source messy --profile advisory --drive data/synthetic/messy_advisory_followup
$PY -m bridge.export_actions --profile advisory --review
$PY -m actions.cli review --profile advisory
```

You get the three numbers kept deliberately apart:

```
baseline_value 288600      (measured, at approval)
expected_value  86580      (PROJECTED at approval — never decides the verdict)
observed_value 249500      (measured, now)   →  effective: true, -13.5%
```

### 1c. Validate, and only then learn

```powershell
$IID = "<the intervention_id from the review output>"
$PY -m actions.cli validate --profile advisory --intervention-id $IID --effective yes --by Emmett
$PY -m pipeline.learn --profile advisory --promote
```

Only now does the fix reach `data/learned/learned_resolutions_advisory.json` and
the `sme_resolutions` collection. Everything approved-but-unvalidated sits in
`data/learned/pending_resolutions_advisory.json`, which is never embedded.

Prove the gate holds:

```powershell
Get-Content data/learned/pending_resolutions_advisory.json   # approvals — not knowledge
Get-Content data/learned/learned_resolutions_advisory.json   # validated only
```

---

## 2. Foyle International — SME #1, contrasting workflow

```powershell
$PY ingest.py --source messy --profile foyle
$PY -m bridge.export_messy --profile foyle --offline
$PY -m bridge.export_actions --profile foyle
```

Same commands, same code, different vocabulary (bookings, placements, host
families). Its drive was seeded for structural bottlenecks rather than
operational ones, so the queue is mostly process interventions and data fixes —
which is itself the honest result.

## 3. McCrossan Joinery — SME #2

```powershell
$PY ingest.py --source messy --profile joinery
$PY -m bridge.export_messy --profile joinery --offline
$PY -m bridge.export_actions --profile joinery
```

**The generalisability moment:** run all three back to back and point out that
nothing but `--profile` changed. No reader, detector, action or ranking code is
per-SME; each profile is a config block, a synthetic drive and an approved mapping.

---

## 4. The dashboard

```powershell
# terminal 1 — API
cd ../hitl-react/api ; .venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
# terminal 2 — UI
cd ../hitl-react ; npm run dev            # http://localhost:5173
```

Open **Today**. Walk it in this order:

1. **The impact strip** — needs attention / revenue at risk / cost of delay /
   staff time, with the analysis date and a data-confidence score.
2. **Expand an item** — the affected engagements, the evidence with spreadsheet
   row references, the recommended action and steps, the impact basis in words.
3. **The execution notice** — before approving, the card states plainly whether
   approving changes files. Show a case action (it does not) next to the data
   fix (it does, to copies).
4. **"See exactly what was sent to the AI"** — the scrubbed payload, with the
   placeholders highlighted. Most queue items were never sent anywhere.
5. **Approve with an owner and a due date**, then walk the progress controls.
6. **"What we did about it"** — the intervention board: baseline vs projected vs
   now, the outcome verdict, and the two buttons that decide whether it becomes
   trusted advice.
7. **Switch SME** from the header to show the same view over a different business.

---

## 5. Evaluation (the dissertation numbers)

```powershell
$PY -m eval.score_mapping                              # all profiles
$PY -m eval.score_detection --profile advisory         # per profile
$PY -m eval.replay --profile foyle                     # longitudinal, outcome-gated
$PY -m eval.plot_replay --profile foyle
$PY -m pytest -q
```

Two caveats to state out loud rather than paper over:

- `advisory`'s mapping proposal was generated offline, so its baseline and "LLM"
  conditions are the same heuristic until `audit.run --profile advisory` is run
  online.
- `mappings/approved_foyle.json` was re-approved in the browser and now scores
  0.800 rather than 1.000. `git checkout mappings/approved_foyle.json` restores
  the committed figure.
