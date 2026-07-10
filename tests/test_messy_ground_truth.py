"""Invariants of the messy synthetic drives, parametrised over every SME
profile — the same assertions holding for both profiles IS the
generalisability claim in test form.

Pinned properties: the ground-truth mapping describes exactly the files on
disk, each drive contains a renamed-header fork and a cross-file duplicate,
and the seeded bottlenecks are recoverable through the true mapping — both
directly and through the full mapped-reader -> generic-detector loop.
"""

from __future__ import annotations

import json
from itertools import combinations

import pandas as pd
import pytest

import config

PROFILES = sorted(config.MESSY_PROFILES)


@pytest.fixture(params=PROFILES)
def profile(request) -> str:
    return request.param


def _gt_mapping(profile: str) -> dict:
    return json.loads(config.MESSY_PROFILES[profile]["gt_mapping"]
                      .read_text(encoding="utf-8"))


def _gt_bottlenecks(profile: str) -> dict:
    return json.loads(config.MESSY_PROFILES[profile]["gt_bottlenecks"]
                      .read_text(encoding="utf-8"))


def _events_frame(profile: str, gt_file: dict) -> pd.DataFrame:
    """Read one events-role file and canonicalise it via its ground-truth columns."""
    drive = config.MESSY_PROFILES[profile]["dir"]
    df = pd.read_excel(drive / gt_file["filename"], dtype=str)
    df = df.rename(columns=gt_file["columns"])
    df["stage"] = df["activity"].fillna("").str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], dayfirst=True, errors="coerce",
                              format="mixed")
    return df


def _events_files(profile: str, include_only: bool = False) -> list[dict]:
    return [f for f in _gt_mapping(profile)["files"]
            if f["role"] == "events" and (f["include"] or not include_only)]


def test_drive_files_match_ground_truth_mapping(profile: str) -> None:
    gt_names = {f["filename"] for f in _gt_mapping(profile)["files"]}
    on_disk = {p.name for p in config.MESSY_PROFILES[profile]["dir"].glob("*.xlsx")}
    assert gt_names == on_disk


def test_drive_contains_a_renamed_header_fork(profile: str) -> None:
    """At least one pair of events files shares NO headers — the seasonal/
    personal fork the alias baseline cannot follow."""
    files = _events_files(profile)
    header_sets = {f["filename"]: set(f["columns"]) for f in files}
    assert any(header_sets[a["filename"]].isdisjoint(header_sets[b["filename"]])
               for a, b in combinations(files, 2))
    for f in files:
        drive = config.MESSY_PROFILES[profile]["dir"]
        actual = set(pd.read_excel(drive / f["filename"], dtype=str, nrows=0).columns)
        assert actual == set(f["columns"]), f["filename"]


def test_drive_contains_a_cross_file_duplicate(profile: str) -> None:
    """Some canonical event rows appear in more than one events file — the
    stale-copy pattern the mapped reader's dedup must absorb."""
    keysets = []
    for f in _events_files(profile):
        df = _events_frame(profile, f)
        keysets.append(set(zip(df["case_id"], df["stage"], df["ts"])))
    assert any(a & b for a, b in combinations(keysets, 2))


def test_foyle_june_is_a_gap() -> None:
    ts = pd.concat([_events_frame("foyle", f) for f in _events_files("foyle")])["ts"]
    assert not ts.isna().any()
    assert ts[(ts >= "2026-06-01") & (ts < "2026-07-01")].empty
    assert (ts < "2026-06-01").any() and (ts >= "2026-07-01").any()


def _detected_by_type(df: pd.DataFrame, profile: str) -> dict[str, set[str]]:
    """Union of affected cases per pattern type from the dynamic detector."""
    from detection.dynamic import detect_dynamic

    out: dict[str, set[str]] = {"delay": set(), "repetition": set(), "rework": set()}
    for bn in detect_dynamic(df, config.MESSY_PROFILES[profile]["stage_order"]):
        out[bn.type] |= set(bn.affected_cases)
    return out


def test_seeded_patterns_are_structurally_detectable(profile: str) -> None:
    """The seeded bottlenecks are STRUCTURAL (outlier gaps, duplicate stage
    entries, backward transitions) — the dynamic detector recovers exactly
    the ground-truth case sets from the data alone, no marker config."""
    gt = _gt_bottlenecks(profile)["bottlenecks"]
    frames = [_events_frame(profile, f) for f in _events_files(profile, include_only=True)]
    df = (pd.concat(frames, ignore_index=True)
          .drop_duplicates(subset=["case_id", "stage", "ts"]))
    df["source_ref"] = "gt"  # detector carries refs; the raw frames have none
    assert df["case_id"].nunique() == _gt_bottlenecks(profile)["cases"]

    detected = _detected_by_type(df, profile)
    for kind in ("delay", "repetition", "rework"):
        assert detected[kind] == set(gt[kind]), kind


def test_end_to_end_rediscovery_via_mapped_reader(profile: str) -> None:
    """The full loop the thesis claims, identical for every profile:
    ground-truth mapping as the approved mapping -> mapped reader (incl.
    dedup) -> DYNAMIC detector (no markers) == seeded truth."""
    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    gt_map = _gt_mapping(profile)
    approved = ApprovedMapping(
        profile=profile, approved_at="2026-07-05T00:00:00+00:00",
        source_proposal_generated_at="2026-07-05T00:00:00+00:00",
        files=[ApprovedFileMapping(
            filename=f["filename"], sheet=f["sheet"], role=f["role"],
            include=f["include"], columns=f["columns"]) for f in gt_map["files"]],
    )
    event_rows, doc_rows = read_mapped(config.MESSY_PROFILES[profile]["dir"], approved)
    assert doc_rows, "reference sheet should feed the RAG corpus"

    df = pd.DataFrame(event_rows)
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    detected = _detected_by_type(df, profile)

    gt = _gt_bottlenecks(profile)["bottlenecks"]
    for kind in ("delay", "repetition", "rework"):
        assert detected[kind] == set(gt[kind]), kind