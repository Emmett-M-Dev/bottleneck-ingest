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
