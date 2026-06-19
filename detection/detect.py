"""Bottleneck detection over the event log.

Reads `outputs/event_log.parquet` (one row per case/stage event) and finds three
bottleneck patterns that match the seeded ground truth:

    delay       — a stage that takes far longer than it should
                  (gap between the prior event and the target stage exceeds a threshold)
    repetition  — a stage whose mere presence means data was entered twice
    rework      — a loop-back stage (a step revised after it was first done)

Repetition and rework are structural: a case is flagged iff it contains the marker
stage at all. Delay is temporal: measured per case as the day gap before the target
stage. Stage labels arrive with inconsistent casing/whitespace ("ENQUIRY", "Enquiry",
"enquiry ") so every comparison is against a canonicalised (stripped, lower-cased) form.

No PII is read here — the parquet's `actor` column is already masked upstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import config


@dataclass
class DetectedBottleneck:
    id: str
    type: str                       # delay | repetition | rework
    stage: str                      # canonical stage label (human-readable)
    affected_cases: list[str]
    metric_label: str               # e.g. "avg_delay_days"
    metric_value: float
    example_refs: list[str] = field(default_factory=list)  # source_refs for evidence

    @property
    def affected_count(self) -> int:
        return len(self.affected_cases)


def _canon(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower()


def load_event_log(path=None) -> pd.DataFrame:
    df = pd.read_parquet(path or config.EVENT_LOG_PATH)
    df["stage"] = _canon(df["activity"])
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _detect_presence(df: pd.DataFrame, stage_canon: str) -> tuple[list[str], list[str]]:
    """Cases that contain `stage_canon` at all, plus their source_refs."""
    hits = df[df["stage"] == stage_canon]
    cases = sorted(hits["case_id"].unique().tolist())
    return cases, hits["source_ref"].head(3).tolist()


def _detect_delay(df: pd.DataFrame, stage_canon: str, threshold_days: int):
    """Cases where the day-gap into `stage_canon` from the preceding event exceeds
    the threshold. Returns (cases, mean_gap_over_flagged, example_refs)."""
    flagged: list[str] = []
    gaps: list[float] = []
    refs: list[str] = []
    for case_id, g in df.sort_values("ts").groupby("case_id"):
        g = g.reset_index(drop=True)
        for i in g.index[g["stage"] == stage_canon]:
            if i == 0 or pd.isna(g["ts"][i]) or pd.isna(g["ts"][i - 1]):
                continue
            gap = (g["ts"][i] - g["ts"][i - 1]).days
            if gap >= threshold_days:
                flagged.append(case_id)
                gaps.append(gap)
                if len(refs) < 3:
                    refs.append(g["source_ref"][i])
                break
    mean_gap = round(sum(gaps) / len(gaps), 1) if gaps else 0.0
    return sorted(set(flagged)), mean_gap, refs


def detect_all(df: pd.DataFrame | None = None) -> list[DetectedBottleneck]:
    if df is None:
        df = load_event_log()

    results: list[DetectedBottleneck] = []

    delay_cases, mean_gap, delay_refs = _detect_delay(
        df, config.DELAY_STAGE.lower(), config.DELAY_THRESHOLD_DAYS
    )
    results.append(DetectedBottleneck(
        id="BN001", type="delay", stage=config.DELAY_STAGE,
        affected_cases=delay_cases, metric_label="avg_delay_days",
        metric_value=mean_gap, example_refs=delay_refs,
    ))

    rep_cases, rep_refs = _detect_presence(df, config.REPETITION_STAGE.lower())
    results.append(DetectedBottleneck(
        id="BN002", type="repetition", stage=config.REPETITION_STAGE,
        affected_cases=rep_cases, metric_label="duplicate_entry_cases",
        metric_value=float(len(rep_cases)), example_refs=rep_refs,
    ))

    rew_cases, rew_refs = _detect_presence(df, config.REWORK_STAGE.lower())
    results.append(DetectedBottleneck(
        id="BN003", type="rework", stage=config.REWORK_STAGE,
        affected_cases=rew_cases, metric_label="rework_loop_cases",
        metric_value=float(len(rew_cases)), example_refs=rew_refs,
    ))

    return results
