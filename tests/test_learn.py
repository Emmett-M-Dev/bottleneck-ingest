"""Learning-loop tests on the pure-JSON half (no chroma, no model).

The behaviour under test is the outcome gate: an approval is recorded but is
NOT knowledge, an ineffective intervention never becomes knowledge, and a
validated-effective one becomes knowledge exactly once. The old tests asserted
the opposite — that approval alone promoted a fix — and have been rewritten
rather than deleted, because that behaviour was the bug.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import config
from actions.execute import create_intervention
from actions.models import ActionItem, AnalysisSnapshot
from actions.outcome import review, validate
from actions import lifecycle, store
from pipeline.learn import (build_resolution, migrate_legacy, promote_validated,
                            record_approval, sync_from_interventions)

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

_KEY = "delay::booking confirmed::avg_delay_days"


def _item() -> ActionItem:
    return ActionItem(
        action_id="ACT-FOY-0001", profile="foyle", finding_key=_KEY,
        finding_type="delay", title="Cases stall entering Booking Confirmed",
        summary="5 cases waited an average of 15 days.",
        stage="Booking Confirmed", affected_case_ids=["B-1", "B-2"],
        metric_label="avg_delay_days", metric_value=15.0,
        action_category="process_intervention",
        recommended_action="Set an entry SLA for Booking Confirmed",
        action_steps=["Agree a target.", "Flag overdue cases."],
    )


def _snapshot(metrics: dict, snapshot_id="SNAP-A", present=None) -> AnalysisSnapshot:
    # source_drive="" explicit — these fixtures stand in for a plain, clean
    # ingest, and several of them are passed straight into
    # actions.outcome.review(), which now refuses an unknown-provenance
    # snapshot (the model's bare default is `None`, not "").
    return AnalysisSnapshot(
        snapshot_id=snapshot_id, profile="foyle", taken_at="2026-07-10",
        source_drive="",
        metrics=metrics, present_keys=list(metrics) if present is None else present)


def _completed_intervention(tmp_path: Path):
    """An intervention a human has approved, owned, worked and marked done."""
    path = tmp_path / "interventions.json"
    intervention = create_intervention(_item(), snapshot=_snapshot({_KEY: 15.0}))
    for status in ("assigned", "in_progress", "completed"):
        lifecycle.transition(intervention, status)
    store.save_interventions("foyle", [intervention], path)
    return path, intervention


# ── Approvals go to the pending store, and stop there ────────────────────────

def test_approval_is_recorded_as_pending_not_as_knowledge(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    entry = record_approval("foyle", _DECISION, path)
    assert entry is not None
    assert _CORPUS_KEYS <= set(entry)
    assert entry["resolution_id"].startswith("RES-PND-")
    assert entry["source"] == "pending"
    assert entry["status"] == "approved_unmeasured"
    assert "Not yet measured" in entry["outcome"]
    assert json.loads(path.read_text(encoding="utf-8")) == [entry]


def test_replayed_approvals_are_deduped_on_decision_id(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    assert record_approval("foyle", _DECISION, path) is not None
    assert record_approval("foyle", _DECISION, path) is None
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


def test_reject_records_nothing(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    assert record_approval("foyle", {**_DECISION, "action": "reject"}, path) is None
    assert not path.exists()


def test_modified_fix_wins_over_the_original_snapshot() -> None:
    decision = {**_DECISION, "action": "modify",
                "modified_fix": {"summary": "Weekly SLA stand-up",
                                 "steps": ["Meet Mondays."],
                                 "rationale": "Cadence."}}
    entry = build_resolution("foyle", decision, 0)
    assert "Weekly SLA stand-up" in entry["action_taken"]
    assert "edited by the reviewer" in entry["outcome"]


# ── The outcome gate ─────────────────────────────────────────────────────────

def test_approved_but_unmeasured_is_not_promoted(tmp_path: Path) -> None:
    learned = tmp_path / "learned.json"
    intervention = create_intervention(_item(), snapshot=_snapshot({_KEY: 15.0}))
    assert promote_validated("foyle", intervention, learned) is None
    assert not learned.exists()


def test_completed_but_unvalidated_is_not_promoted(tmp_path: Path) -> None:
    learned = tmp_path / "learned.json"
    path, intervention = _completed_intervention(tmp_path)
    review("foyle", _snapshot({}, "SNAP-B", present=[]), path=path)
    measured = store.load_interventions("foyle", path)[0]

    assert measured.outcome.effective is True       # the system's reading...
    assert promote_validated("foyle", measured, learned) is None   # ...is not enough
    assert not learned.exists()


def test_ineffective_intervention_is_never_promoted(tmp_path: Path) -> None:
    learned = tmp_path / "learned.json"
    path, intervention = _completed_intervention(tmp_path)
    # The metric got worse.
    review("foyle", _snapshot({_KEY: 30.0}, "SNAP-B"), path=path)
    validate("foyle", intervention.intervention_id, effective=False,
             validated_by="Emmett", path=path)

    added = sync_from_interventions(
        "foyle", interventions=store.load_interventions("foyle", path), path=learned)
    assert added == []
    assert not learned.exists()
    # ...but the audit trail survives in full.
    stored = store.load_interventions("foyle", path)[0]
    assert stored.status == "ineffective"
    assert [e.to_status for e in stored.history][-1] == "ineffective"


def test_validated_effective_is_promoted_once_and_is_idempotent(tmp_path: Path) -> None:
    learned = tmp_path / "learned.json"
    path, intervention = _completed_intervention(tmp_path)
    review("foyle", _snapshot({}, "SNAP-B", present=[]), path=path)
    validate("foyle", intervention.intervention_id, effective=True,
             validated_by="Emmett", path=path)

    items = store.load_interventions("foyle", path)
    first = sync_from_interventions("foyle", interventions=items, path=learned)
    second = sync_from_interventions("foyle", interventions=items, path=learned)

    assert len(first) == 1 and second == []
    entries = json.loads(learned.read_text(encoding="utf-8"))
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source"] == "learned"
    assert entry["status"] == "validated_effective"
    assert entry["intervention_id"] == intervention.intervention_id
    # The measurement travels with the entry — that evidence is why it is trusted.
    assert "Validated as effective" in entry["outcome"]


def test_rejected_intervention_is_never_promoted(tmp_path: Path) -> None:
    learned = tmp_path / "learned.json"
    intervention = create_intervention(_item())
    intervention.status = "proposed"
    intervention.history = intervention.history[:1]
    lifecycle.transition(intervention, "rejected", note="not our problem")
    assert promote_validated("foyle", intervention, learned) is None


# ── Migration off the old behaviour ──────────────────────────────────────────

def test_migration_demotes_approval_only_entries(tmp_path: Path) -> None:
    learned = tmp_path / "learned.json"
    pending = tmp_path / "pending.json"
    learned.write_text(json.dumps([
        {"resolution_id": "RES-LRN-FOY-001", "profile": "foyle",
         "bottleneck_type": "delay", "stage": "Booking Confirmed",
         "problem_description": "old", "action_taken": "old",
         "outcome": "Approved at HITL Gate 2 on 2026-07-10.",
         "days_to_resolve": None, "source": "learned",
         "decision_id": "BN001-approve-test1"},
        {"resolution_id": "RES-LRN-FOY-002", "profile": "foyle",
         "bottleneck_type": "rework", "stage": "Placement Offer",
         "problem_description": "new", "action_taken": "new",
         "outcome": "Validated as effective on 2026-07-20.",
         "days_to_resolve": 10, "source": "learned",
         "intervention_id": "INT-FOY-abc12345"},
    ]), encoding="utf-8")

    stats = migrate_legacy("foyle", learned=learned, pending=pending)
    assert stats == {"moved": 1, "kept": 1}

    kept = json.loads(learned.read_text(encoding="utf-8"))
    assert [e["resolution_id"] for e in kept] == ["RES-LRN-FOY-002"]
    demoted = json.loads(pending.read_text(encoding="utf-8"))
    assert demoted[0]["resolution_id"] == "RES-PND-FOY-001"
    assert "approval alone is not evidence" in demoted[0]["outcome"]


def test_learn_import_is_light() -> None:
    """The heavy stack loads only inside embed_learned() — the repo rule."""
    code = ("import sys, pipeline.learn; "
            "bad = [m for m in ('chromadb', 'torch', 'spacy') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=str(config.ROOT), timeout=120)
