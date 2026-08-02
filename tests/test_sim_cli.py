import json

from simulator.cli import main


def _run(argv, capsys):
    main(argv)
    return json.loads(capsys.readouterr().out)


def test_reset_writes_day_zero_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    out = _run(["--profile", "advisory", "--reset"], capsys)
    assert out["day"] == 0
    assert (tmp_path / "advisory" / "state.json").exists()


def test_advance_persists_state_across_invocations(tmp_path, monkeypatch,
                                                   capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "2"], capsys)
    out = _run(["--profile", "advisory", "--advance", "1"], capsys)
    assert out["day"] == 3
    assert json.loads((tmp_path / "advisory" / "state.json")
                      .read_text(encoding="utf-8"))["day"] == 3


def test_advance_writes_the_drive_and_the_inbox(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "3"], capsys)
    assert (tmp_path / "advisory" / "drive" / "leads.xlsx").exists()
    lines = (tmp_path / "advisory" / "inbox.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert lines and all(json.loads(ln)["msg_id"] for ln in lines)


def test_status_does_not_advance_the_clock(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("config.DATA_SIM", tmp_path)
    _run(["--profile", "advisory", "--reset"], capsys)
    _run(["--profile", "advisory", "--advance", "2"], capsys)
    out = _run(["--profile", "advisory", "--status"], capsys)
    assert out["day"] == 2
    assert out["cases"] > 0
