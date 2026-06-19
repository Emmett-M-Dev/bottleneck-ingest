"""Detection tests on a hand-built event log — deterministic, no parquet needed.

Covers the three patterns and the casing-noise tolerance (stage labels arrive as
"ENQUIRY"/"Enquiry"/"enquiry ").
"""

from __future__ import annotations

import pandas as pd

import config
from detection.detect import detect_all


def _df(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp", "actor", "status", "source_ref"])
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _by_id(results):
    return {b.id: b for b in results}


def test_repetition_and_rework_detected_by_presence() -> None:
    df = _df([
        ("C1", "Payment", "2026-01-01", None, "ok", "r1"),
        ("C1", "Payment Re-entry", "2026-01-02", None, "ok", "r2"),   # repetition marker
        ("C2", "Quote", "2026-01-01", None, "ok", "r3"),
        ("C2", "QUOTE REVISION", "2026-01-03", None, "ok", "r4"),     # rework, noisy casing
        ("C3", "Enquiry", "2026-01-01", None, "ok", "r5"),            # clean case, no markers
    ])
    res = _by_id(detect_all(df))
    assert res["BN002"].affected_cases == ["C1"]
    assert res["BN003"].affected_cases == ["C2"]


def test_delay_flags_only_cases_over_threshold() -> None:
    thresh = config.DELAY_THRESHOLD_DAYS
    df = _df([
        # slow: big gap into booking confirmation -> flagged
        ("S1", "Quote", "2026-01-01", None, "ok", "s1"),
        ("S1", "Booking Confirmation", f"2026-01-{1 + thresh + 3:02d}", None, "ok", "s2"),
        # fast: 1-day gap -> not flagged
        ("F1", "Quote", "2026-01-01", None, "ok", "f1"),
        ("F1", "booking confirmation", "2026-01-02", None, "ok", "f2"),
    ])
    res = _by_id(detect_all(df))
    assert res["BN001"].affected_cases == ["S1"]
    assert res["BN001"].metric_value >= thresh


def test_all_three_bottlenecks_always_returned() -> None:
    res = _by_id(detect_all(_df([("C1", "Enquiry", "2026-01-01", None, "ok", "r1")])))
    assert set(res) == {"BN001", "BN002", "BN003"}
    assert all(b.affected_count == 0 for b in res.values())  # nothing matched
