"""LLM anomaly pass — a local model reads aggregate stage statistics and
proposes findings the three structural detectors don't look for.

    stats   = build_stage_stats(df, stage_order)      # numbers + stage names ONLY
    findings = propose_anomalies(stats, domain=...)   # [] if Ollama is absent

Strictly advisory: findings surface in the UI as "AI-spotted — unverified"
cards, are never evaluated against ground truth, and never gate anything.
That is why this call site runs on the free local model (pipeline/llm.py)
rather than Claude — see the division-of-labour note there.

Privacy by construction: the payload contains stage labels and aggregate
numbers only — no cell values, no case ids, no actors, no status strings.
There is a test asserting exactly that. With local inference on top, nothing
leaves the machine at all.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from pipeline.llm import complete_json

MAX_FINDINGS = 3


class AnomalyFinding(BaseModel):
    title: str = Field(description="short headline for the pattern")
    stage: str = Field(description="the stage the pattern centres on, from the provided list")
    narrative: str = Field(description="2-3 plain-language sentences: what looks unusual and why it might matter")
    severity: Literal["low", "medium", "high"]


class AnomalyReport(BaseModel):
    findings: list[AnomalyFinding]


SYSTEM_PROMPT = (
    "You are an operations analyst reviewing aggregate workflow statistics "
    "for a small business. You see per-stage numbers only — no records. "
    "Report up to {max_findings} genuinely unusual patterns that a simple "
    "delay/repetition/rework scan would MISS: e.g. a stage most cases skip, "
    "a stage with wildly inconsistent timing, an odd volume imbalance "
    "between adjacent stages, or a stage where cases silently end. "
    "Only report what the numbers support; if nothing stands out, return an "
    "empty findings list. Use only stage names from the provided list."
)


def build_stage_stats(df: pd.DataFrame, stage_order: list[str]) -> dict:
    """Aggregate per-stage statistics. Stage names and numbers only — this
    dict is the entire LLM payload, so nothing row-level may enter it."""
    total_cases = int(df["case_id"].nunique())

    # Entry gaps per stage (days from the case's previous event).
    gaps_by_stage: dict[str, list[float]] = {}
    for _, g in df.sort_values("ts").groupby("case_id"):
        g = g.reset_index(drop=True)
        for i in range(1, len(g)):
            if pd.isna(g["ts"][i]) or pd.isna(g["ts"][i - 1]):
                continue
            gaps_by_stage.setdefault(g["stage"][i], []).append(
                float((g["ts"][i] - g["ts"][i - 1]).days))

    # Repeats and backward entries per stage.
    repeat_cases = (df.groupby(["case_id", "stage"]).size()
                    .reset_index(name="n").query("n >= 2")
                    .groupby("stage")["case_id"].nunique().to_dict())
    order_index = {s.strip().lower(): i for i, s in enumerate(stage_order)}
    backward: dict[str, int] = {}
    for _, g in df.sort_values("ts").groupby("case_id"):
        max_seen = -1
        for stage in g["stage"]:
            idx = order_index.get(stage)
            if idx is None:
                continue
            if idx < max_seen:
                backward[stage] = backward.get(stage, 0) + 1
            max_seen = max(max_seen, idx)

    stages = []
    for stage in sorted(df["stage"].unique()):
        hits = df[df["stage"] == stage]
        gaps = gaps_by_stage.get(stage, [])
        s = pd.Series(gaps, dtype=float) if gaps else None
        stages.append({
            "stage": stage,
            "events": int(len(hits)),
            "cases": int(hits["case_id"].nunique()),
            "share_of_cases": round(hits["case_id"].nunique() / total_cases, 2)
                              if total_cases else 0.0,
            "entry_gap_days": None if s is None else {
                "min": float(s.min()), "median": float(s.median()),
                "max": float(s.max()), "mean": round(float(s.mean()), 1),
            },
            "cases_with_repeats": int(repeat_cases.get(stage, 0)),
            "backward_entries": int(backward.get(stage, 0)),
            "distinct_status_values": int(hits["status"].fillna("").astype(str)
                                          .str.strip().str.lower().nunique()),
        })

    return {
        "total_cases": total_cases,
        "total_events": int(len(df)),
        "expected_stage_order": [s.strip().lower() for s in stage_order],
        "stages": stages,
    }


def propose_anomalies(stats: dict, *, domain: str = "small business operations",
                      client=None) -> list[AnomalyFinding]:
    """Ask the local model for advisory findings. [] when Ollama is absent or
    the reply doesn't validate — the export must never depend on this."""
    system = SYSTEM_PROMPT.format(max_findings=MAX_FINDINGS) + \
        f"\nBusiness domain: {domain}."
    report = complete_json(system, stats, AnomalyReport, client=client)
    if report is None:
        return []
    valid_stages = {s["stage"] for s in stats["stages"]}
    return [f for f in report.findings if f.stage in valid_stages][:MAX_FINDINGS]
