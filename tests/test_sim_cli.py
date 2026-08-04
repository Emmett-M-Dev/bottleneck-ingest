import json

import pytest

import simulator.cli as cli_mod
from actions.models import ActionItem
from simulator.cli import main


def _run(argv, capsys):
    main(argv)
    return json.loads(capsys.readouterr().out)


def _isolate(monkeypatch, tmp_path):
    """Every test that can reach `--advance` must never read the real
    outputs/actions_<profile>.json in the working tree: `approved_items`
    falls back to `config.OUTPUTS` whenever `--approved-from` is not
    passed, so a test that only patches `config.DATA_SIM` is silently
    exercising `worker.apply_approved` against whatever the dashboard's
    Gate 2 last approved. Patching `config.OUTPUTS` to an empty tmp
    directory makes every test hermetic regardless of product state."""
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    monkeypatch.setattr("config.OUTPUTS", tmp_path / "outputs")


def test_reset_writes_day_zero_state(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    out = _run(["--profile", "advisory", "--reset"], capsys)
    assert out["day"] == 0
    assert (tmp_path / "advisory" / "state.json").exists()


def test_advance_persists_state_across_invocations(tmp_path, monkeypatch,
                                                   capsys):
    _isolate(monkeypatch, tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "2"], capsys)
    out = _run(["--profile", "advisory", "--advance", "1"], capsys)
    assert out["day"] == 3
    assert json.loads((tmp_path / "advisory" / "state.json")
                      .read_text(encoding="utf-8"))["day"] == 3


def test_advance_writes_the_drive_and_the_inbox(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "3"], capsys)
    assert (tmp_path / "advisory" / "drive" / "leads.xlsx").exists()
    lines = (tmp_path / "advisory" / "inbox.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert lines and all(json.loads(ln)["msg_id"] for ln in lines)


def test_status_does_not_advance_the_clock(tmp_path, monkeypatch, capsys):
    _isolate(monkeypatch, tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "2"], capsys)
    out = _run(["--profile", "advisory", "--status"], capsys)
    assert out["day"] == 2
    assert out["cases"] > 0


def test_advance_resumes_cleanly_after_a_mid_batch_interruption(
        tmp_path, monkeypatch, capsys):
    """Killing the process partway through a multi-day --advance is a real
    scenario for a CLI designed to be shelled out to by an orchestrator with
    a timeout. `msg_id` is deterministic per (seed, day)
    (compose.py: f"M{day:03d}-{seq:02d}"), so a naive resume that replays an
    already-completed day appends EXACT DUPLICATE well-formed lines to
    inbox.jsonl -- not corruption, so an atomic write would not catch it.
    The fix is commit granularity: save_world must run after EACH day, not
    once after the whole batch, so a day that has already written its
    inbox/drive entries is never re-run.

    Forces a hard failure on the 3rd call to `advance` -- i.e. before ANY
    of day 3's work (message compose, inbox append, drive render, state
    save) has started -- so days 1-2 are fully committed and day 3 never
    began. Resuming for the remaining days must therefore continue from day
    3, not replay days 1-2.
    """
    _isolate(monkeypatch, tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)

    real_advance = cli_mod.advance
    calls = {"n": 0}

    def _flaky_advance(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash before day 3 starts")
        return real_advance(*a, **kw)

    monkeypatch.setattr(cli_mod, "advance", _flaky_advance)
    with pytest.raises(RuntimeError):
        main(["--profile", "advisory", "--advance", "5"])
    capsys.readouterr()  # the crashed call printed nothing; reset the buffer

    state = json.loads((tmp_path / "advisory" / "state.json")
                       .read_text(encoding="utf-8"))
    assert state["day"] == 2, "days 1-2 must have committed before the crash"

    monkeypatch.setattr(cli_mod, "advance", real_advance)
    out = _run(["--profile", "advisory", "--advance", "3"], capsys)
    assert out["day"] == 5

    lines = (tmp_path / "advisory" / "inbox.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    msg_ids = [json.loads(ln)["msg_id"] for ln in lines]
    assert len(msg_ids) == len(set(msg_ids)), (
        "resume duplicated an already-committed day's messages")


def test_approved_action_item_changes_a_days_effects(tmp_path, monkeypatch,
                                                      capsys):
    """approved_items / --approved-from exist to feed worker.apply_approved
    -- the arrow that makes outcome measurement possible (worker.py's own
    docstring). Nothing else in this file exercises that path. Proves it
    end-to-end THROUGH THE CLI: the same first day, advanced once with no
    approvable action items and once with one actionable ActionItem
    pointing at a real day-0 case, must produce different `effects`."""
    _isolate(monkeypatch, tmp_path)

    _run(["--profile", "advisory", "--reset"], capsys)
    baseline = _run(["--profile", "advisory", "--advance", "1"], capsys)
    assert baseline["days"][0]["effects"] == []

    state = json.loads((tmp_path / "advisory" / "state.json")
                       .read_text(encoding="utf-8"))
    case_id = next(iter(state["cases"]))

    item = ActionItem(
        action_id="ACT-SIM-0001", profile="advisory",
        finding_key="stalled_case::sim-test", finding_type="stalled_case",
        title="Stalled case (test)", affected_case_ids=[case_id],
        action_category="case_action", status="approved",
        created_at="2026-01-01", updated_at="2026-01-01",
    )
    approvals_path = tmp_path / "approvals.json"
    approvals_path.write_text(json.dumps([item.model_dump()]),
                              encoding="utf-8")

    _run(["--profile", "advisory", "--reset"], capsys)
    out = _run(["--profile", "advisory", "--advance", "1",
               "--approved-from", str(approvals_path)], capsys)
    effects = out["days"][0]["effects"]
    assert any(e["case_id"] == case_id and e["finding_type"] == "stalled_case"
              and e["outcome"] in ("applied", "failed") for e in effects)
