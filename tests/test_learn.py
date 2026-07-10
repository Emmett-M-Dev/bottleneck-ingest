"""Learning-loop tests on the pure-JSON half (no chroma, no model): approvals
append a corpus-shaped entry, rejects don't, replays dedup on decision_id,
edited fixes win over the original snapshot, and `import pipeline.learn`
stays light.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import config
from pipeline.learn import append_learned, build_resolution

_DECISION = {
    "decision_id": "BN001-approve-123",
    "case_id": "BN001",
    "action": "approve",
    "original_fix_snapshot": {
        "summary": "Set an entry SLA for Booking Confirmed",
        "steps": ["Agree a target.", "Flag overdue cases."],
        "rationale": "Stalls become checkpoints.",
    },
    "modified_fix": None,
    "decided_at": "2026-07-10T09:00:00.000Z",
    "time_to_decision_seconds": 12.5,
    "profile": "foyle",
    "case_snapshot": {
        "type": "delay", "stage": "Booking Confirmed",
        "title": "Cases stall entering Booking Confirmed",
        "description": "5 cases waited an average of 15 days.",
    },
}

_CORPUS_KEYS = {"resolution_id", "profile", "bottleneck_type", "stage",
                "problem_description", "action_taken", "outcome",
                "days_to_resolve", "source"}


def test_approve_appends_corpus_shaped_entry(tmp_path: Path) -> None:
    path = tmp_path / "learned.json"
    entry = append_learned("foyle", _DECISION, path)
    assert entry is not None
    assert _CORPUS_KEYS <= set(entry)
    assert entry["resolution_id"] == "RES-LRN-FOY-001"
    assert entry["source"] == "learned"
    assert entry["bottleneck_type"] == "delay"
    assert "Set an entry SLA" in entry["action_taken"]
    assert json.loads(path.read_text(encoding="utf-8")) == [entry]


def test_replay_is_deduped_on_decision_id(tmp_path: Path) -> None:
    path = tmp_path / "learned.json"
    assert append_learned("foyle", _DECISION, path) is not None
    assert append_learned("foyle", _DECISION, path) is None
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1
    # A different decision gets the next id.
    other = {**_DECISION, "decision_id": "BN002-approve-456",
             "case_snapshot": {**_DECISION["case_snapshot"], "type": "rework"}}
    entry = append_learned("foyle", other, path)
    assert entry["resolution_id"] == "RES-LRN-FOY-002"


def test_reject_learns_nothing(tmp_path: Path) -> None:
    path = tmp_path / "learned.json"
    assert append_learned("foyle", {**_DECISION, "action": "reject"}, path) is None
    assert not path.exists()


def test_modified_fix_wins_over_snapshot() -> None:
    decision = {**_DECISION, "action": "modify",
                "modified_fix": {"summary": "Weekly SLA stand-up",
                                 "steps": ["Meet Mondays."],
                                 "rationale": "Cadence."}}
    entry = build_resolution("foyle", decision, 0)
    assert "Weekly SLA stand-up" in entry["action_taken"]
    assert "edited by the reviewer" in entry["outcome"]


def test_learn_import_is_light() -> None:
    """The heavy stack loads only inside embed_learned() — the repo rule."""
    code = ("import sys, pipeline.learn; "
            "bad = [m for m in ('chromadb', 'torch', 'spacy') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=str(config.ROOT), timeout=120)
