"""Dynamic-detector tests on hand-built event logs: each structural pattern is
found without any marker config, a clean log yields nothing, min_affected
filters singletons, and the anomaly-pass payload contains aggregates only
(no cell values). No parquet, no chroma, no LLM.
"""

from __future__ import annotations

import json

import pandas as pd

from detection.anomaly import build_stage_stats, propose_anomalies
from detection.dynamic import detect_dynamic, gap_threshold_days

STAGE_ORDER = ["Request Received", "Placement Offer", "Booking Confirmed",
               "Invoice Issued", "Arrival"]


def _df(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp",
                                     "actor", "status", "source_ref"])
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _fast_case(case_id: str, start_day: int) -> list[tuple]:
    """A clean 1-day-per-step case — the population that defines 'normal'."""
    return [(case_id, stage, f"2026-01-{start_day + i:02d}", "[ACTOR]", "ok",
             f"{case_id}-r{i}") for i, stage in enumerate(STAGE_ORDER)]


def _population(n: int = 6) -> list[tuple]:
    rows: list[tuple] = []
    for i in range(n):
        rows.extend(_fast_case(f"OK{i}", 1 + i * 2))
    return rows


def test_delay_outlier_found_without_markers() -> None:
    rows = _population()
    for cid, start in (("SLOW1", "2026-01-01"), ("SLOW2", "2026-01-02")):
        rows += [
            (cid, "Placement Offer", start, "[ACTOR]", "ok", f"{cid}-r0"),
            # ~3 weeks into Booking Confirmed vs the population's 1-day steps
            (cid, "Booking Confirmed", "2026-01-24", "[ACTOR]", "ok", f"{cid}-r1"),
        ]
    found = detect_dynamic(_df(rows), STAGE_ORDER)
    delays = [b for b in found if b.type == "delay"]
    assert len(delays) == 1
    assert delays[0].stage == "Booking Confirmed"
    assert set(delays[0].affected_cases) == {"SLOW1", "SLOW2"}
    assert delays[0].metric_value > gap_threshold_days(_df(_population()))


def test_literal_repeat_found() -> None:
    rows = _population()
    for cid in ("R1", "R2"):
        rows += [
            (cid, "Request Received", "2026-02-01", "[ACTOR]", "ok", f"{cid}-r0"),
            (cid, "Placement Offer", "2026-02-02", "[ACTOR]", "ok", f"{cid}-r1"),
            (cid, "Placement Offer", "2026-02-03", "[ACTOR]", "ok", f"{cid}-r2"),
        ]
    found = detect_dynamic(_df(rows), STAGE_ORDER)
    reps = [b for b in found if b.type == "repetition"]
    assert len(reps) == 1
    assert reps[0].stage == "Placement Offer"
    assert set(reps[0].affected_cases) == {"R1", "R2"}


def test_backward_loop_found() -> None:
    rows = _population()
    for cid in ("W1", "W2"):
        rows += [
            (cid, "Placement Offer", "2026-02-01", "[ACTOR]", "ok", f"{cid}-r0"),
            (cid, "Booking Confirmed", "2026-02-02", "[ACTOR]", "ok", f"{cid}-r1"),
            (cid, "Placement Offer", "2026-02-03", "[ACTOR]", "ok", f"{cid}-r2"),  # back!
        ]
    found = detect_dynamic(_df(rows), STAGE_ORDER)
    rework = [b for b in found if b.type == "rework"]
    assert len(rework) == 1
    assert rework[0].stage == "Placement Offer"
    assert set(rework[0].affected_cases) == {"W1", "W2"}
    # Disjoint semantics: the backward revisit is rework ONLY — it must not
    # double-report as repetition even though the stage occurs twice.
    assert [b for b in found if b.type == "repetition"] == []


def test_clean_log_yields_nothing() -> None:
    assert detect_dynamic(_df(_population()), STAGE_ORDER) == []


def test_min_affected_filters_singletons() -> None:
    rows = _population() + [
        ("ONE", "Request Received", "2026-02-01", "[ACTOR]", "ok", "o0"),
        ("ONE", "Request Received", "2026-02-02", "[ACTOR]", "ok", "o1"),
    ]
    assert detect_dynamic(_df(rows), STAGE_ORDER) == []
    found = detect_dynamic(_df(rows), STAGE_ORDER, min_affected=1)
    assert [b.type for b in found] == ["repetition"]


def test_ids_ordered_by_impact() -> None:
    rows = _population()
    for i in range(3):  # 3 cases repeat one stage
        cid = f"R{i}"
        rows += [(cid, "Invoice Issued", f"2026-03-0{1 + j}", "[ACTOR]", "ok",
                  f"{cid}-r{j}") for j in range(2)]
    for cid in ("W1", "W2"):  # 2 cases loop back
        rows += [
            (cid, "Placement Offer", "2026-02-01", "[ACTOR]", "ok", f"{cid}-r0"),
            (cid, "Booking Confirmed", "2026-02-02", "[ACTOR]", "ok", f"{cid}-r1"),
            (cid, "Placement Offer", "2026-02-03", "[ACTOR]", "ok", f"{cid}-r2"),
        ]
    found = detect_dynamic(_df(rows), STAGE_ORDER)
    assert found[0].id == "BN001" and found[0].affected_count == 3
    assert all(a.affected_count >= b.affected_count
               for a, b in zip(found, found[1:]))


def test_stage_stats_payload_is_aggregate_only() -> None:
    """The anomaly payload must leak no row-level values: no case ids, actors,
    status strings, or source refs — stage names and numbers only."""
    rows = _population() + [
        ("SECRET-CASE-9", "Placement Offer", "2026-02-01",
         "Una Toner", "chased by una.toner@foyle.example", "secret-ref-1"),
    ]
    stats = build_stage_stats(_df(rows), STAGE_ORDER)
    blob = json.dumps(stats)
    for leak in ("SECRET-CASE-9", "Una Toner", "una.toner", "secret-ref-1",
                 "chased"):
        assert leak not in blob, leak
    assert stats["total_cases"] == 7
    assert all(set(s) >= {"stage", "events", "cases"} for s in stats["stages"])


def test_propose_anomalies_absent_ollama_is_empty() -> None:
    """No local model -> silently no findings (the export must not break)."""
    import httpx

    def refuse(request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    stats = build_stage_stats(_df(_population()), STAGE_ORDER)
    assert propose_anomalies(stats, client=client) == []
