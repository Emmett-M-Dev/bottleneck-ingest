from detection.detect import DetectedBottleneck, finding_key


def _bn(**kw):
    base = dict(id="BN001", type="delay", stage="Proposal",
                affected_cases=["NA-1", "NA-2"], metric_label="avg_delay_days",
                metric_value=18.0, example_refs=[])
    base.update(kw)
    return DetectedBottleneck(**base)


def test_finding_key_is_content_based_not_positional():
    a = _bn(id="BN001")
    b = _bn(id="BN007")
    assert finding_key(a) == finding_key(b)


def test_finding_key_canonicalises_stage_case_and_whitespace():
    assert finding_key(_bn(stage="Proposal")) == finding_key(_bn(stage=" PROPOSAL "))


def test_finding_key_separates_different_findings():
    assert finding_key(_bn(type="delay")) != finding_key(_bn(type="rework"))
    assert finding_key(_bn(stage="Proposal")) != finding_key(_bn(stage="Delivery"))


import pandas as pd
from datetime import datetime

from actions.build import build_action_items


def _events(rows):
    df = pd.DataFrame(rows, columns=["case_id", "activity", "ts", "actor",
                                       "status", "source_ref", "value"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["stage"] = df["activity"].fillna("").astype(str).str.strip().str.lower()
    return df


def test_diagnosis_attaches_by_content_not_by_rank_order():
    """A ui_cases export whose BN ids are in a DIFFERENT order from this run's
    detection must still land its prose on the right finding."""
    rows = []
    # Background cases with normal gaps (1-3 days)
    for n in range(1, 6):
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", "2026-01-01", "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", "2026-01-02", "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", "2026-01-04", "R", "done", "x.xlsx:3", 1000),
        ]
    # Three anomalous cases with long gaps (25+ days) at Proposal stage
    for n in range(6, 9):
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", "2026-01-01", "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", "2026-01-03", "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", "2026-01-28", "R", "done", "x.xlsx:3", 1000),
        ]
    df = _events(rows)

    cases = [{
        "case_id": "BN099",                  # deliberately wrong positional id
        "finding_key": "delay::proposal::avg_delay_days",
        "type": "delay",
        "title": "CONTENT-MATCHED TITLE",
        "description": "matched by content key",
    }]

    items = build_action_items("advisory", df, cases=cases)
    delays = [i for i in items if i.finding_type == "delay"]
    assert delays, "expected a delay finding"
    assert delays[0].title == "CONTENT-MATCHED TITLE"


def test_fallback_only_applies_to_legacy_exports():
    """When a current-style export carries finding_key entries, the positional
    bn.id fallback must NOT apply. A finding whose content key is not in the
    export must not pick up the prose from an unrelated finding at its rank position."""
    rows = []
    # Background cases with normal gaps (1-3 days)
    for n in range(1, 6):
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", "2026-01-01", "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", "2026-01-02", "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", "2026-01-04", "R", "done", "x.xlsx:3", 1000),
            (cid, "Won", "2026-01-05", "R", "done", "x.xlsx:4", 1000),
        ]
    # Anomalous cases with long gaps at Proposal (delay)
    for n in range(6, 9):
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", "2026-01-01", "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", "2026-01-03", "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", "2026-01-28", "R", "done", "x.xlsx:3", 1000),
            (cid, "Won", "2026-01-29", "R", "done", "x.xlsx:4", 1000),
        ]
    # Cases with rework (backward transition from Won to Proposal)
    for n in range(9, 12):
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", "2026-01-01", "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", "2026-01-02", "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", "2026-01-04", "R", "done", "x.xlsx:3", 1000),
            (cid, "Won", "2026-01-05", "R", "done", "x.xlsx:4", 1000),
            (cid, "Proposal", "2026-01-06", "R", "done", "x.xlsx:5", 1000),  # rework
        ]
    df = _events(rows)

    # Current-style export: has finding_key entries
    # case_id="BN002" is the rework's assigned id, but finding_key is for delay
    # This should NOT attach the delay title to the rework
    cases = [
        {
            "case_id": "BN001",
            "finding_key": "delay::proposal::avg_delay_days",
            "type": "delay",
            "title": "DELAY TITLE",
            "description": "delay description",
        },
        {
            "case_id": "BN002",  # This is the rework's id, but content key is delay
            "finding_key": "delay::proposal::avg_delay_days",
            "type": "delay",
            "title": "SHOULD NOT APPEAR",
            "description": "this should not attach to rework",
        },
    ]

    items = build_action_items("advisory", df, cases=cases)
    reworks = [i for i in items if i.finding_type == "rework"]
    assert reworks, "expected a rework finding"
    # The rework should NOT pick up the prose from the mismatched case_id
    assert reworks[0].title != "SHOULD NOT APPEAR"
    # It should have the default auto-generated title, not the export's
    assert "rework" in reworks[0].title.lower()


def test_export_messy_emits_finding_key():
    """Verify that bridge/export_messy.py emits finding_key in case dicts.

    We use static source analysis to avoid the heavy import (spaCy via pipeline.diagnose).
    If someone removes or breaks the finding_key emission, this test fails immediately."""
    import re
    from pathlib import Path

    messy_path = Path(__file__).parent.parent / "bridge" / "export_messy.py"
    source = messy_path.read_text(encoding="utf-8")

    # Check that finding_key is imported
    assert "from detection.detect import finding_key" in source, \
        "finding_key must be imported from detection.detect"

    # Check that the case dict includes finding_key emission
    # Look for the pattern where case_id and finding_key are both assigned
    pattern = r'"case_id":\s*bn\.id,\s*"finding_key":\s*finding_key\(bn\)'
    assert re.search(pattern, source), \
        'case dict must contain "case_id": bn.id, "finding_key": finding_key(bn)'
