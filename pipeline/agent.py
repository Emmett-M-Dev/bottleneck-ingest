"""LangGraph agentic loop: detect -> retrieve -> diagnose -> gate -> execute.

    python -m pipeline.agent --profile foyle [--offline]     # run to the gate
    python -m pipeline.agent --resume foyle_<timestamp>      # after approval

The graph wraps the pipeline core in a stateful loop with HITL Gate 2 built
in as a conditional edge. A fresh run stops at the gate (`awaiting_gate`) and
writes its full state to outputs/agent_run_<profile>_<timestamp>.json — the
auditable artifact. The human then approves/rejects fixes in the dashboard's
Fixes tab (which appends to hitl-interface/logs/decisions.jsonl); `--resume`
reads those decisions, seeds them into the saved state, and re-invokes the
graph. A conditional entry edge routes a resumed state straight to the gate,
so detection and the Claude diagnosis are never repeated.

    START ──(no diagnoses)──> detect -> retrieve -> diagnose ─┐
      └──(diagnoses present)────────────────────────────────> gate
                gate ──approved──> execute -> END
                gate ──otherwise──────────-> END   (awaiting_gate | rejected)

Execute shells out to `remediate.run --apply` as a SEPARATE process — the
repo-wide rule: remediation never shares a process with chromadb/torch, which
this graph's retrieve/diagnose nodes load. For the same reason every heavy
import here is lazy, inside its node: `import pipeline.agent` stays light.
"""

from __future__ import annotations

import os

# Same thread-pinning as pipeline/embed.py — must precede any native libs that
# the nodes lazily load (chromadb/torch via retrieve, spaCy via diagnose).
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

import config


class AgentState(TypedDict, total=False):
    profile: str
    event_log_path: str
    offline: bool
    run_id: str
    bottlenecks: list[dict]            # dataclasses.asdict(DetectedBottleneck) + affected_count
    retrieved: dict[str, list[dict]]   # bottleneck id -> top-3 past resolutions
    diagnoses: dict[str, dict]         # bottleneck id -> DiagnosisResult.model_dump()
    gate_decisions: dict[str, str]     # case id -> approve | modify | reject
    status: str                        # awaiting_gate | approved | rejected | executed
    executed: dict                     # remediate subprocess result


# ── Nodes (heavy imports stay inside — the import-light rule) ────────────────

def detect_node(state: AgentState) -> dict:
    from dataclasses import asdict

    from detection.detect import detect_generic, load_event_log

    markers = config.MESSY_PROFILES[state["profile"]]["markers"]
    detected = detect_generic(load_event_log(), **markers)
    return {
        "event_log_path": str(config.EVENT_LOG_PATH),
        "bottlenecks": [{**asdict(bn), "affected_count": bn.affected_count}
                        for bn in detected],
    }


def retrieve_node(state: AgentState) -> dict:
    from pipeline.diagnose import retrieve_resolutions

    retrieved = {
        bn["id"]: retrieve_resolutions(
            f"{bn['stage']} {bn['type']} bottleneck", state["profile"])
        for bn in state.get("bottlenecks", [])
    }
    return {"retrieved": retrieved}


def diagnose_node(state: AgentState) -> dict:
    from pipeline import diagnose as dg

    diagnoses: dict[str, dict] = {}
    for bn in state.get("bottlenecks", []):
        retrieved = state.get("retrieved", {}).get(bn["id"], [])
        if state.get("offline"):
            result = dg.offline_diagnosis(bn, retrieved)
        else:
            try:
                result = dg.diagnose(bn, state["profile"], retrieved=retrieved)
            except Exception as exc:
                print(f"[agent] diagnose failed for {bn['id']} ({exc}); "
                      f"using the offline fallback")
                result = dg.offline_diagnosis(bn, retrieved)
        diagnoses[bn["id"]] = result.model_dump()
    return {"diagnoses": diagnoses}


def gate_node(state: AgentState) -> dict:
    """HITL Gate 2. No human decisions yet -> pause the run; any approved or
    modified fix -> execute; everything rejected -> end without executing."""
    decisions = state.get("gate_decisions") or {}
    if not decisions:
        return {"status": "awaiting_gate"}
    if any(a in ("approve", "modify") for a in decisions.values()):
        return {"status": "approved"}
    return {"status": "rejected"}


def execute_node(state: AgentState) -> dict:
    """Run the remediation executor in its own process (the repo rule: it must
    never share a process with the chroma/torch this graph has loaded)."""
    cmd = [sys.executable, "-m", "remediate.run",
           "--profile", state["profile"], "--apply"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(config.ROOT),
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    def _tail(text: str) -> str:
        return "\n".join((text or "").strip().splitlines()[-5:])
    return {"status": "executed",
            "executed": {"command": " ".join(cmd[1:]),
                         "returncode": proc.returncode,
                         "stdout_tail": _tail(proc.stdout),
                         "stderr_tail": _tail(proc.stderr)}}


# ── Graph ────────────────────────────────────────────────────────────────────

def build_graph(*, detect_fn: Callable | None = None,
                retrieve_fn: Callable | None = None,
                diagnose_fn: Callable | None = None,
                execute_fn: Callable | None = None):
    """Compile the graph. Node functions are injectable so tests can run the
    real edges with stub nodes (no event log, no chroma, no API, no subprocess)."""
    g = StateGraph(AgentState)
    g.add_node("detect", detect_fn or detect_node)
    g.add_node("retrieve", retrieve_fn or retrieve_node)
    g.add_node("diagnose", diagnose_fn or diagnose_node)
    g.add_node("gate", gate_node)
    g.add_node("execute", execute_fn or execute_node)

    # Resumed states carry diagnoses -> enter at the gate; fresh runs detect.
    g.add_conditional_edges(
        START, lambda s: "gate" if s.get("diagnoses") else "detect",
        {"gate": "gate", "detect": "detect"})
    g.add_edge("detect", "retrieve")
    g.add_edge("retrieve", "diagnose")
    g.add_edge("diagnose", "gate")
    g.add_conditional_edges(
        "gate", lambda s: "execute" if s.get("status") == "approved" else END,
        {"execute": "execute", END: END})
    g.add_edge("execute", END)
    return g.compile()


# ── Gate-decision plumbing (two-phase run) ───────────────────────────────────

def read_gate_decisions(run_started_at: str, case_ids: list[str],
                        log_path: Path | str | None = None) -> dict[str, str]:
    """Latest decision per case from the dashboard's append-only log.

    decisions.jsonl has no profile field and case ids (BN001...) repeat across
    profiles and runs, so only decisions made AFTER this run started count —
    the timestamp filter is what keeps stale/cross-profile decisions out.
    Both timestamps are UTC ISO-8601, so string comparison is safe."""
    log_path = Path(log_path) if log_path else config.DECISIONS_LOG_DEFAULT
    if not log_path.exists():
        return {}
    wanted = set(case_ids)
    decisions: dict[str, str] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (rec.get("case_id") in wanted and rec.get("action")
                and rec.get("decided_at", "") >= run_started_at):
            decisions[rec["case_id"]] = rec["action"]  # later lines win
    return decisions


def run_state_path(run_id: str) -> Path:
    return config.OUTPUTS / f"agent_run_{run_id}.json"


def _write_run_state(run_id: str, state: dict) -> Path:
    path = run_state_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def main() -> None:
    from pipeline.diagnose import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Agentic detect->retrieve->diagnose->gate->execute run")
    parser.add_argument("--profile", choices=sorted(config.MESSY_PROFILES),
                        help="SME profile (fresh run)")
    parser.add_argument("--offline", action="store_true",
                        help="skip the Claude diagnosis; heuristic fallback")
    parser.add_argument("--resume", metavar="RUN_ID",
                        help="resume a paused run after Gate-2 decisions")
    parser.add_argument("--decisions", type=Path, default=None,
                        help=f"decisions log (default: {config.DECISIONS_LOG_DEFAULT})")
    args = parser.parse_args()
    if bool(args.profile) == bool(args.resume):
        parser.error("exactly one of --profile (fresh run) or --resume is required")

    graph = build_graph()

    if args.resume:
        path = run_state_path(args.resume)
        if not path.exists():
            raise SystemExit(f"[agent] no saved run at {path} — run --profile first")
        saved = json.loads(path.read_text(encoding="utf-8"))
        decisions = read_gate_decisions(saved["started_at"],
                                        list(saved.get("diagnoses", {})),
                                        args.decisions)
        saved["gate_decisions"] = decisions
        final = dict(graph.invoke(saved))
        final["resumed_at"] = datetime.now(timezone.utc).isoformat()
        out = _write_run_state(args.resume, final)
        print(f"[agent] resume: {len(decisions)} decision(s) read -> "
              f"status={final['status']} ({out.relative_to(config.ROOT)})")
        if final["status"] == "awaiting_gate":
            print("[agent] Still no Gate-2 decisions for this run — approve/reject "
                  "the fixes in the dashboard's Fixes tab, then re-run --resume.")
        elif final["status"] == "executed":
            print(f"[agent] remediation returncode="
                  f"{final['executed']['returncode']}")
        return

    started_at = datetime.now(timezone.utc)
    run_id = f"{args.profile}_{started_at.strftime('%Y%m%dT%H%M%SZ')}"
    state: AgentState = {
        "profile": args.profile,
        "event_log_path": str(config.EVENT_LOG_PATH),
        "offline": args.offline,
        "run_id": run_id,
    }
    final = dict(graph.invoke(state))
    final["started_at"] = started_at.isoformat()
    out = _write_run_state(run_id, final)
    n_bn = len(final.get("bottlenecks", []))
    print(f"[agent] {run_id}: {n_bn} bottleneck(s) diagnosed -> "
          f"status={final['status']} ({out.relative_to(config.ROOT)})")
    if final["status"] == "awaiting_gate":
        print("[agent] Next: approve/reject the fixes in the dashboard's Fixes "
              f"tab, then:  python -m pipeline.agent --resume {run_id}")


if __name__ == "__main__":
    main()
