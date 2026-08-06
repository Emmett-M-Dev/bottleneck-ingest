"""The CLI's own contract, pinned: "Errors exit non-zero with `{"error": ...}`"
(actions/cli.py's module docstring). Before this fix `ContaminatedBaselineError`
propagated as a raw Python traceback — exit 1, EMPTY stdout — which the
dashboard's `_run_actions_cli` then failed to json.loads() and turned into an
opaque HTTP 500. These tests exercise `main()` exactly as the dashboard shells
out to it (argv in, stdout out), not the underlying functions directly, so a
regression back to a bare traceback would be caught here.
"""

from __future__ import annotations

import json

import pytest

import config
from actions import store
from actions.cli import main
from actions.models import ActionItem, AnalysisSnapshot


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    return tmp_path


def _item() -> ActionItem:
    return ActionItem(
        action_id="ACT-FOY-0001", profile="foyle",
        finding_key="delay::booking confirmed::avg_delay_days",
        finding_type="delay", title="Cases stall entering Booking Confirmed",
        affected_case_ids=["B-001"], metric_label="avg_delay_days",
        metric_value=14.0, action_category="case_action",
        created_at="2026-07-01", updated_at="2026-07-01")


def _run(argv, capsys):
    """Call main() the way the dashboard's subprocess does, and hand back
    (exit_code, parsed stdout) — mirroring tests/test_sim_cli.py's pattern
    for the sibling CLI module."""
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    out = capsys.readouterr().out
    return excinfo.value.code, json.loads(out)


def test_decide_on_a_contaminated_baseline_emits_clean_json_not_a_traceback(
        isolated_outputs, capsys) -> None:
    store.save_actions("foyle", [_item()])
    contaminated = AnalysisSnapshot(
        snapshot_id="SNAP-SIM", profile="foyle", taken_at="2026-07-27",
        source_drive="data/sim/foyle/drive",  # not foyle's default dir
        metrics={"delay::booking confirmed::avg_delay_days": 14.0},
        present_keys=["delay::booking confirmed::avg_delay_days"])
    store.append_snapshot("foyle", contaminated)

    code, payload = _run(
        ["decide", "--profile", "foyle", "--action-id", "ACT-FOY-0001",
         "--decision", "approve", "--owner", "Ciara"], capsys)

    assert code == 1
    assert payload["ok"] is False
    assert "error" in payload and payload["error"]
    assert "foyle" in payload["error"]
    # Nothing must have been persisted off the back of a refused approval.
    assert store.load_interventions("foyle") == []


def test_decide_on_an_unknown_provenance_baseline_also_emits_clean_json(
        isolated_outputs, capsys) -> None:
    store.save_actions("foyle", [_item()])
    legacy = AnalysisSnapshot(
        snapshot_id="SNAP-LEGACY", profile="foyle", taken_at="2026-07-20",
        metrics={"delay::booking confirmed::avg_delay_days": 14.0},
        present_keys=["delay::booking confirmed::avg_delay_days"])
    assert legacy.source_drive is None  # the model default: unknown provenance
    store.append_snapshot("foyle", legacy)

    code, payload = _run(
        ["decide", "--profile", "foyle", "--action-id", "ACT-FOY-0001",
         "--decision", "approve", "--owner", "Ciara"], capsys)

    assert code == 1
    assert payload["ok"] is False
    assert "error" in payload and payload["error"]


def test_review_on_a_contaminated_snapshot_emits_clean_json_not_a_traceback(
        isolated_outputs, capsys) -> None:
    """The review command reaches the SAME guard from the observation side —
    see actions/outcome.py::review."""
    contaminated = AnalysisSnapshot(
        snapshot_id="SNAP-SIM", profile="foyle", taken_at="2026-07-27",
        source_drive="data/sim/foyle/drive",
        metrics={}, present_keys=[])
    store.append_snapshot("foyle", contaminated)

    code, payload = _run(["review", "--profile", "foyle"], capsys)

    assert code == 1
    assert payload["ok"] is False
    assert "error" in payload and payload["error"]


def test_decide_on_a_clean_default_drive_baseline_still_succeeds(
        isolated_outputs, capsys) -> None:
    """The guard must not become a blanket refusal — the ordinary, undamaged
    path (source_drive == "", the profile's own default) still approves."""
    store.save_actions("foyle", [_item()])
    clean = AnalysisSnapshot(
        snapshot_id="SNAP-CLEAN", profile="foyle", taken_at="2026-07-27",
        source_drive="",
        metrics={"delay::booking confirmed::avg_delay_days": 14.0},
        present_keys=["delay::booking confirmed::avg_delay_days"])
    store.append_snapshot("foyle", clean)

    # The success path returns normally — main() only calls sys.exit() on
    # the _fail() branch — so, unlike the refusal tests above, no
    # SystemExit is expected here.
    main(["decide", "--profile", "foyle", "--action-id", "ACT-FOY-0001",
          "--decision", "approve", "--owner", "Ciara"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["baseline_value"] == 14.0
