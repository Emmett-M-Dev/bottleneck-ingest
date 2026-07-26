"""LangGraph agent tests: the graph runs to the gate and pauses, the approved
edge fires execute (and only then), rejection ends the run, resumed states
skip detection, the decisions-log filter, and the import-light rule — all with
stub nodes: no event log, no chroma, no API, no subprocess.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import config
from pipeline.agent import build_graph, read_gate_decisions

_BN = {"id": "BN001", "type": "delay", "stage": "Booking Confirmed",
       "metric_label": "avg_delay_days", "metric_value": 11.5,
       "affected_cases": ["B-001"], "example_refs": [], "affected_count": 1}

_DIAGNOSIS = {"diagnosis": "Bookings wait on host matching.",
              "root_cause": "Hosts recruited late.",
              "suggested_fix": {"summary": "Recruit earlier",
                                "steps": ["Recruit in February."],
                                "rationale": "Worked before."},
              "confidence": 0.8,
              "retrieved_resolution_ids": ["RES-FOY-001"]}


class _Calls:
    """Stub node factory that counts invocations."""

    def __init__(self) -> None:
        self.detect = 0
        self.execute = 0

    def detect_fn(self, state: dict) -> dict:
        self.detect += 1
        return {"bottlenecks": [_BN]}

    def retrieve_fn(self, state: dict) -> dict:
        return {"retrieved": {"BN001": [{"resolution_id": "RES-FOY-001"}]}}

    def diagnose_fn(self, state: dict) -> dict:
        return {"diagnoses": {"BN001": _DIAGNOSIS}}

    def execute_fn(self, state: dict) -> dict:
        self.execute += 1
        return {"status": "executed", "executed": {"returncode": 0}}

    def graph(self):
        return build_graph(detect_fn=self.detect_fn, retrieve_fn=self.retrieve_fn,
                           diagnose_fn=self.diagnose_fn, execute_fn=self.execute_fn)


def test_graph_pauses_at_gate() -> None:
    calls = _Calls()
    final = calls.graph().invoke({"profile": "foyle", "offline": True,
                                  "run_id": "t"})
    assert final["status"] == "awaiting_gate"
    assert calls.detect == 1
    assert calls.execute == 0            # nothing executes without a human
    assert final["diagnoses"]["BN001"]["confidence"] == 0.8


def test_approved_edge_fires_execute() -> None:
    calls = _Calls()
    final = calls.graph().invoke({
        "profile": "foyle", "run_id": "t",
        "bottlenecks": [_BN], "diagnoses": {"BN001": _DIAGNOSIS},
        "gate_decisions": {"BN001": "approve"},
    })
    assert final["status"] == "executed"
    assert calls.execute == 1
    assert calls.detect == 0             # resumed state entered at the gate


def test_modify_counts_as_approval() -> None:
    calls = _Calls()
    final = calls.graph().invoke({
        "profile": "foyle", "run_id": "t",
        "diagnoses": {"BN001": _DIAGNOSIS},
        "gate_decisions": {"BN001": "modify"},
    })
    assert final["status"] == "executed"
    assert calls.execute == 1


def test_rejected_ends_without_execute() -> None:
    calls = _Calls()
    final = calls.graph().invoke({
        "profile": "foyle", "run_id": "t",
        "diagnoses": {"BN001": _DIAGNOSIS},
        "gate_decisions": {"BN001": "reject"},
    })
    assert final["status"] == "rejected"
    assert calls.execute == 0


def test_read_gate_decisions_filters(tmp_path: Path) -> None:
    """Stale (pre-run) and other-case decisions are ignored; the latest
    decision per case wins."""
    log = tmp_path / "decisions.jsonl"
    log.write_text("\n".join([
        json.dumps({"case_id": "BN001", "action": "approve",
                    "decided_at": "2026-07-01T09:00:00+00:00"}),   # stale run
        json.dumps({"case_id": "BN009", "action": "approve",
                    "decided_at": "2026-07-09T12:00:00+00:00"}),   # other case
        json.dumps({"case_id": "BN001", "action": "reject",
                    "decided_at": "2026-07-09T12:01:00+00:00"}),
        json.dumps({"case_id": "BN001", "action": "approve",
                    "decided_at": "2026-07-09T12:05:00+00:00"}),   # latest wins
        "not json",
    ]), encoding="utf-8")
    decisions = read_gate_decisions("2026-07-09T11:00:00+00:00", ["BN001"], log)
    assert decisions == {"BN001": "approve"}


def test_read_gate_decisions_missing_log(tmp_path: Path) -> None:
    assert read_gate_decisions("2026-07-09T11:00:00+00:00", ["BN001"],
                               tmp_path / "nope.jsonl") == {}


def test_execute_node_does_not_remediate_for_an_operational_fix() -> None:
    """The behavioural correction: approving a delay fix (a process change)
    must not run the status-normalising executor over the drive."""
    from pipeline.agent import execute_node

    result = execute_node({"profile": "foyle", "bottlenecks": [_BN],
                           "gate_decisions": {"BN001": "approve"}})["executed"]
    assert result["remediation_invoked"] is False
    assert "remediation" not in result
    assert result["routed"] == [{"case_id": "BN001", "finding_type": "delay",
                                 "category": "process_intervention",
                                 "route": "tracked"}]
    assert result["tracked"] == 1


def test_execute_node_reports_why_nothing_ran() -> None:
    from pipeline.agent import execute_node

    result = execute_node({"profile": "foyle", "bottlenecks": [_BN],
                           "gate_decisions": {"BN001": "modify"}})["executed"]
    assert "no files were touched" in result["reason"]


def test_agent_import_is_light() -> None:
    """`import pipeline.agent` must not pull the heavy native stack — checked
    in a fresh interpreter so other tests' imports can't mask a violation."""
    code = ("import sys, pipeline.agent; "
            "bad = [m for m in ('chromadb', 'torch', 'spacy') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=str(config.ROOT), timeout=120)
