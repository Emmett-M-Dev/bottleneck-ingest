# How to Run

This page shows how to run the pipeline and the dashboard. The commands target Windows and PowerShell, because that is the build machine.

## Before you start

- Python, with the pinned requirements installed in a venv at `.venv`.
- Optional: an Anthropic API key in a local `.env` file, for the agent steps. Without it, use the offline flags.
- Optional: Ollama with a local model, for the anomaly pass. Without it, the pass skips.

On Windows, call the venv Python by its full path. The bare `python` command hits a Store stub. Set the output encoding first, because some status values use characters that the default code page rejects.

```
$env:PYTHONIOENCODING="utf-8"
```

## The light path (no API key)

Every agent step has an offline mode. This runs the whole pipeline with no API key, at lower accuracy. It uses heuristics and templates instead of the model.

```
.venv/Scripts/python.exe -m audit.run --profile foyle --offline
# review and approve in the dashboard Mapping Review tab
.venv/Scripts/python.exe ingest.py --source messy --profile foyle
.venv/Scripts/python.exe -m bridge.export_messy --profile foyle --offline
.venv/Scripts/python.exe -m remediate.run --profile foyle
.venv/Scripts/python.exe -m eval.score_mapping --profile foyle
```

## The full path (with API key)

Drop the `--offline` flags to use the Claude agents for mapping and diagnosis.

```
$env:PYTHONIOENCODING="utf-8"
.venv/Scripts/python.exe -m audit.run --profile foyle
# review and approve in the dashboard Mapping Review tab
.venv/Scripts/python.exe ingest.py --source messy --profile foyle
.venv/Scripts/python.exe -m bridge.export_messy --profile foyle
.venv/Scripts/python.exe -m remediate.run --profile foyle --apply
.venv/Scripts/python.exe -m eval.score_mapping --profile foyle
```

The ingest step errors on purpose if there is no approved mapping. This enforces Gate 1.

## Switch to the second firm

Swap the profile. There is no new code. This is the generalisability point.

```
.venv/Scripts/python.exe -m audit.run --profile joinery --offline
# approve, then run the same steps with --profile joinery
```

## The dashboard

Run the backend and the UI in two terminals.

```
# terminal 1 — backend
cd hitl-react/api
.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000

# terminal 2 — UI
cd hitl-react
npm run dev
```

The UI serves at `http://localhost:5173`. Once each firm has been built once, the dashboard switches between them at once.

## The longitudinal replay

This is the over-time evaluation. It is eval-side only and does not touch the pipeline core.

```
.venv/Scripts/python.exe synthetic/generate_stream.py --profile foyle
.venv/Scripts/python.exe -m eval.replay --profile foyle
.venv/Scripts/python.exe -m eval.plot_replay --profile foyle
```

This writes nine weekly snapshots, replays them, and draws the result curves into the outputs folder. Add `--llm` to use the model during replay. The [Evaluation](Evaluation) page explains the curves.

## If something breaks

- If eval numbers move without reason, an approval in the browser may have overwritten the approved mapping. Restore it from git.
- If a status value crashes the console, set the UTF-8 encoding shown above.
