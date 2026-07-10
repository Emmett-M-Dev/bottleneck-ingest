"""Dynamic bottleneck detection — statistical scan over every stage.

Unlike detect_generic (which checks three marker stages named in config), this
detector derives everything from the event log itself:

    delay       — a stage whose entry gaps are outliers against the log's own
                  gap distribution (threshold = Q3 + 1.5*IQR over all
                  transition gaps, floored at 1 day)
    repetition  — a stage that occurs two or more times within a case
    rework      — a stage a case RETURNS to after having progressed past it
                  (a backward transition against the profile's stage_order)

A stage is reported only when at least `min_affected` cases show the pattern,
so one odd row doesn't become a bottleneck. The result is 0..N bottlenecks —
the count is a property of the data, not the config. `markers` in
config.MESSY_PROFILES now exist solely so the eval can compare this detector
against the old marker baseline.

Reuses DetectedBottleneck / load_event_log from detection.detect; no PII is
read (the `actor` column is masked upstream and never touched here).
"""

from __future__ import annotations

import pandas as pd

from detection.detect import DetectedBottleneck


def _canon(label: str) -> str:
    return str(label).strip().lower()


def gap_threshold_days(df: pd.DataFrame, *, floor_days: float = 1.0) -> float:
    """Robust outlier threshold over ALL per-case transition gaps: Q3 + 1.5*IQR.
    The log defines its own notion of "too slow" — no per-SME tuning."""
    gaps: list[float] = []
    for _, g in df.sort_values("ts").groupby("case_id"):
        ts = g["ts"].dropna().tolist()
        gaps.extend((b - a).days for a, b in zip(ts, ts[1:]))
    if not gaps:
        return floor_days
    s = pd.Series(gaps, dtype=float)
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    return max(float(q3 + 1.5 * (q3 - q1)), floor_days)


def _delay_stages(df: pd.DataFrame, threshold: float) -> dict[str, dict]:
    """stage -> {cases, gaps, refs} where the entry gap into the stage from the
    case's previous event EXCEEDS the threshold (one hit per case per stage).
    Strictly greater: with a uniform gap distribution IQR is 0 and the
    threshold collapses onto the common gap itself — >= would then flag every
    normal step as a delay."""
    out: dict[str, dict] = {}
    for case_id, g in df.sort_values("ts").groupby("case_id"):
        g = g.reset_index(drop=True)
        seen: set[str] = set()
        for i in range(1, len(g)):
            if pd.isna(g["ts"][i]) or pd.isna(g["ts"][i - 1]):
                continue
            gap = (g["ts"][i] - g["ts"][i - 1]).days
            stage = g["stage"][i]
            if gap > threshold and stage not in seen:
                seen.add(stage)
                d = out.setdefault(stage, {"cases": [], "gaps": [], "refs": []})
                d["cases"].append(case_id)
                d["gaps"].append(gap)
                if len(d["refs"]) < 3:
                    d["refs"].append(g["source_ref"][i])
    return out


def _repetition_stages(df: pd.DataFrame) -> dict[str, dict]:
    """stage -> cases in which that stage occurs 2+ times."""
    out: dict[str, dict] = {}
    counts = df.groupby(["case_id", "stage"]).agg(
        n=("stage", "size"), ref=("source_ref", "last")).reset_index()
    for _, row in counts[counts["n"] >= 2].iterrows():
        d = out.setdefault(row["stage"], {"cases": [], "repeats": [], "refs": []})
        d["cases"].append(row["case_id"])
        d["repeats"].append(int(row["n"]))
        if len(d["refs"]) < 3:
            d["refs"].append(row["ref"])
    return out


def _rework_stages(df: pd.DataFrame, stage_order: list[str]) -> dict[str, dict]:
    """stage -> cases that transitioned BACK to it after progressing past it.
    Stages outside stage_order carry no ordering, so they can't loop."""
    index = {_canon(s): i for i, s in enumerate(stage_order)}
    out: dict[str, dict] = {}
    for case_id, g in df.sort_values("ts").groupby("case_id"):
        g = g.reset_index(drop=True)
        max_seen = -1
        flagged: set[str] = set()
        for i in range(len(g)):
            stage = g["stage"][i]
            if stage not in index:
                continue
            idx = index[stage]
            if idx < max_seen and stage not in flagged:
                flagged.add(stage)
                d = out.setdefault(stage, {"cases": [], "refs": []})
                d["cases"].append(case_id)
                if len(d["refs"]) < 3:
                    d["refs"].append(g["source_ref"][i])
            max_seen = max(max_seen, idx)
    return out


def detect_dynamic(df: pd.DataFrame, stage_order: list[str], *,
                   min_affected: int = 2) -> list[DetectedBottleneck]:
    """Scan every stage for the three structural patterns; N results ordered
    by impact (affected cases, then metric), ids BN001..N."""
    labels = {_canon(s): s for s in stage_order}

    def label(stage: str) -> str:
        return labels.get(stage, stage.title())

    threshold = gap_threshold_days(df)
    found: list[DetectedBottleneck] = []

    for stage, d in _delay_stages(df, threshold).items():
        if len(d["cases"]) < min_affected:
            continue
        found.append(DetectedBottleneck(
            id="", type="delay", stage=label(stage),
            affected_cases=sorted(set(d["cases"])),
            metric_label="avg_delay_days",
            metric_value=round(sum(d["gaps"]) / len(d["gaps"]), 1),
            example_refs=d["refs"],
        ))

    for stage, d in _repetition_stages(df).items():
        if len(d["cases"]) < min_affected:
            continue
        found.append(DetectedBottleneck(
            id="", type="repetition", stage=label(stage),
            affected_cases=sorted(set(d["cases"])),
            metric_label="repeated_entry_cases",
            metric_value=float(len(set(d["cases"]))),
            example_refs=d["refs"],
        ))

    for stage, d in _rework_stages(df, stage_order).items():
        if len(d["cases"]) < min_affected:
            continue
        found.append(DetectedBottleneck(
            id="", type="rework", stage=label(stage),
            affected_cases=sorted(set(d["cases"])),
            metric_label="loop_back_cases",
            metric_value=float(len(set(d["cases"]))),
            example_refs=d["refs"],
        ))

    # Impact order: most cases first, metric breaks ties; then stable ids.
    found.sort(key=lambda b: (-b.affected_count, -b.metric_value, b.stage))
    for i, bn in enumerate(found, start=1):
        bn.id = f"BN{i:03d}"
    return found
