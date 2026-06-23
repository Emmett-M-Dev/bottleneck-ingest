"""Background poller — keep the dashboard's JSON export in sync with the live Drive sheets.

Every `--interval` seconds: read the six Foyle sheets from the Drive folder, scrub +
detect, and re-export `ui_*.json`. The dashboard (which only ever reads JSON) reflects
the change on its next auto-refresh. Detection and PII scrubbing stay server-side, so
nothing raw or heavy crosses to the dashboard.

This gives the "live" feel without the dashboard ever touching Drive:

    edit a Drive cell  ->  poller re-runs (one pass)  ->  dashboard updates in seconds

Run it in its own terminal alongside the dashboard:
    python -m bridge.refresh_foyle --interval 30
    python -m bridge.refresh_foyle --once      # single pass (smoke test)
"""

from __future__ import annotations

import argparse
import time
import traceback
from datetime import datetime

import ingest
from bridge import export_foyle


def _cycle() -> tuple[int, dict]:
    ingest.run("foyle-sheets")               # read Drive -> scrub -> embed -> event log
    n, workflow = export_foyle.export()      # detect -> ui_cases.json + ui_workflow.json
    return n, workflow["kpis"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Poll the Foyle Drive sheets and refresh the dashboard export")
    ap.add_argument("--interval", type=int, default=30, help="seconds between refreshes")
    ap.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = ap.parse_args()

    while True:
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            n, kpis = _cycle()
            print(f"[{ts}] refreshed: {n} cases · kpis={kpis}")
        except Exception as exc:  # keep polling through transient Drive/auth errors
            print(f"[{ts}] refresh FAILED: {exc}")
            traceback.print_exc()
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
