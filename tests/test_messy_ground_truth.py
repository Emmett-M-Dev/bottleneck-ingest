"""Generator-level invariants for the messy Foyle drive (data/synthetic/messy_foyle).

The drive is the mapping-agent's test bed, so these tests pin the properties the
audit agent and the mapped reader depend on: the ground-truth mapping describes
exactly the files on disk, the seasonal fork really does rename every header,
the stale OLD file really is a row-subset of the NEW one, June really is a gap,
and the seeded bottleneck cases really carry the pattern the detectors look for.
End-to-end rediscovery (mapping -> ingest -> detect == ground truth) is covered
separately once the mapped reader exists.
"""

from __future__ import annotations

import json

import pandas as pd

import config

_PROFILE = config.MESSY_PROFILES["foyle"]
_DRIVE = _PROFILE["dir"]


def _gt_mapping() -> dict:
    return json.loads(_PROFILE["gt_mapping"].read_text(encoding="utf-8"))


def _gt_bottlenecks() -> dict:
    return json.loads(_PROFILE["gt_bottlenecks"].read_text(encoding="utf-8"))


def _events_frame(gt_file: dict) -> pd.DataFrame:
    """Read one events-role file and canonicalise it via its ground-truth columns."""
    df = pd.read_excel(_DRIVE / gt_file["filename"], dtype=str)
    df = df.rename(columns=gt_file["columns"])
    df["stage"] = df["activity"].fillna("").str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], dayfirst=True, errors="coerce", format="mixed")
    return df


def test_drive_files_match_ground_truth_mapping() -> None:
    gt_names = {f["filename"] for f in _gt_mapping()["files"]}
    on_disk = {p.name for p in _DRIVE.glob("*.xlsx")}
    assert gt_names == on_disk


def test_fork_renames_every_header() -> None:
    files = {f["filename"]: f for f in _gt_mapping()["files"]}
    canon = set(files["bookings Jan-May 2026.xlsx"]["columns"])
    fork = set(files["bookings summer NEW.xlsx"]["columns"])
    assert canon.isdisjoint(fork)
    for name in ("bookings Jan-May 2026.xlsx", "bookings summer NEW.xlsx"):
        headers = set(pd.read_excel(_DRIVE / name, dtype=str, nrows=0).columns)
        assert headers == set(files[name]["columns"])


def test_old_file_is_duplicate_subset_of_new() -> None:
    files = {f["filename"]: f for f in _gt_mapping()["files"]}
    old = _events_frame(files["bookings summer OLD do not use.xlsx"])
    new = _events_frame(files["bookings summer NEW.xlsx"])
    new_keys = set(zip(new["case_id"], new["stage"], new["ts"]))
    old_keys = set(zip(old["case_id"], old["stage"], old["ts"]))
    assert old_keys and old_keys <= new_keys


def test_june_is_a_gap() -> None:
    files = [f for f in _gt_mapping()["files"] if f["role"] == "events"]
    ts = pd.concat([_events_frame(f)["ts"] for f in files])
    assert not ts.isna().any()
    in_june = ts[(ts >= "2026-06-01") & (ts < "2026-07-01")]
    assert in_june.empty
    # ...but both sides of the gap are populated
    assert (ts < "2026-06-01").any() and (ts >= "2026-07-01").any()


def test_seeded_patterns_are_detectable_with_the_true_mapping() -> None:
    gt = _gt_bottlenecks()["bottlenecks"]
    files = [f for f in _gt_mapping()["files"]
             if f["role"] == "events" and f["include"]]
    df = pd.concat([_events_frame(f) for f in files], ignore_index=True)
    assert df["case_id"].nunique() == _gt_bottlenecks()["cases"]

    # delay: the gap into Booking Confirmed crosses the threshold iff seeded
    threshold = _PROFILE["markers"]["delay_threshold_days"]
    for case_id, g in df.sort_values("ts").groupby("case_id"):
        g = g.reset_index(drop=True)
        idx = g.index[g["stage"] == "booking confirmed"]
        assert len(idx) == 1 and idx[0] > 0
        gap = (g["ts"][idx[0]] - g["ts"][idx[0] - 1]).days
        assert (gap >= threshold) == (case_id in gt["delay"]), case_id

    # repetition / rework: marker presence iff seeded
    for kind, marker in (("repetition", "document re-request"),
                         ("rework", "placement re-allocation")):
        flagged = set(df[df["stage"] == marker]["case_id"])
        assert flagged == set(gt[kind]), kind
