"""F2: the event-log owner stamp must distinguish a simulated/alternate-drive
ingest from the profile's default static drive, so a later run can tell
whose data `outputs/event_log.parquet` (the one global file) actually holds.

`ingest` is imported lazily inside each test, matching the existing
lazy-import convention at its only other call site
(`bridge/export_actions.py::_assert_event_log_belongs_to`) rather than at
module scope, since `ingest.py` pulls in the embedding/chroma stack.
"""

from __future__ import annotations

from pathlib import Path


def test_owner_stamp_unchanged_when_drive_is_absent(tmp_path, monkeypatch):
    import config
    import ingest

    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    ingest._write_event_log_owner("messy", "advisory", drive=None)
    stamped = ingest.event_log_owner_path().read_text(encoding="utf-8")
    assert stamped == "advisory"


def test_owner_stamp_records_the_drive_path_when_drive_is_passed(
        tmp_path, monkeypatch):
    import config
    import ingest

    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    ingest._write_event_log_owner(
        "messy", "advisory", drive=Path("data/sim/advisory/drive"))
    stamped = ingest.event_log_owner_path().read_text(encoding="utf-8")
    assert stamped != "advisory", (
        "a --drive ingest must be distinguishable from the default static "
        "drive on disk")
    assert stamped.startswith("advisory@")
    assert "data/sim/advisory/drive" in stamped


def test_owner_stamp_falls_back_to_source_when_no_profile(tmp_path, monkeypatch):
    """Unchanged pre-existing behaviour: no profile falls back to source,
    with or without a drive."""
    import config
    import ingest

    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    ingest._write_event_log_owner("local", None, drive=None)
    assert ingest.event_log_owner_path().read_text(encoding="utf-8") == "local"
