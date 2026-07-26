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
    # `value` is the optional monetary column; a log written before it existed
    # simply has none, and every consumer treats absent as unknown.
    if "value" not in df.columns:
        df["value"] = pd.NA
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
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


def _detect_invoice_payment_delay(df: pd.DataFrame, threshold_days: int):
    """Foyle delay: per case, the gap between its Invoice Issued and Payment Received
    events. Pairs the two events directly (rather than using the generic prior-event
    gap) so the measure is always invoice->payment regardless of event interleaving."""
    flagged: list[str] = []
    gaps: list[float] = []
    refs: list[str] = []
    for case_id, g in df.groupby("case_id"):
        inv = g[g["stage"] == "invoice issued"].sort_values("ts")
        pay = g[g["stage"] == "payment received"].sort_values("ts")
        if inv.empty or pay.empty:
            continue
        i_ts, p_ts = inv["ts"].iloc[0], pay["ts"].iloc[0]
        if pd.isna(i_ts) or pd.isna(p_ts):
            continue
        gap = (p_ts - i_ts).days
        if gap >= threshold_days:
            flagged.append(case_id)
            gaps.append(gap)
            if len(refs) < 3:
                refs.append(pay["source_ref"].iloc[0])
    mean_gap = round(sum(gaps) / len(gaps), 1) if gaps else 0.0
    return sorted(set(flagged)), mean_gap, refs


def detect_foyle(df: pd.DataFrame | None = None) -> list[DetectedBottleneck]:
    """Detection for the multi-sheet Foyle model. Delay = invoice->payment gap;
    repetition = Document Re-request present; rework = Placement Re-allocation present.
    Reuses the presence helper; the algorithm is shared, only the markers differ."""
    if df is None:
        df = load_event_log()

    delay_cases, mean_gap, delay_refs = _detect_invoice_payment_delay(
        df, config.FOYLE_DELAY_THRESHOLD_DAYS
    )
    rep_cases, rep_refs = _detect_presence(df, config.FOYLE_REPETITION_STAGE.lower())
    rew_cases, rew_refs = _detect_presence(df, config.FOYLE_REWORK_STAGE.lower())

    return [
        DetectedBottleneck(
            id="BN001", type="delay", stage=config.FOYLE_DELAY_STAGE,
            affected_cases=delay_cases, metric_label="avg_days_to_pay",
            metric_value=mean_gap, example_refs=delay_refs,
        ),
        DetectedBottleneck(
            id="BN002", type="repetition", stage=config.FOYLE_REPETITION_STAGE,
            affected_cases=rep_cases, metric_label="doc_rerequest_cases",
            metric_value=float(len(rep_cases)), example_refs=rep_refs,
        ),
        DetectedBottleneck(
            id="BN003", type="rework", stage=config.FOYLE_REWORK_STAGE,
            affected_cases=rew_cases, metric_label="reallocation_cases",
            metric_value=float(len(rew_cases)), example_refs=rew_refs,
        ),
    ]


def _status_lower(value) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip().lower()


def _detect_tracker_leadtime(df: pd.DataFrame, threshold_days: int):
    """Tracker delay: cohorts where a milestone (accommodation / placement) was
    locked within `threshold_days` of arrival, or after it. Lead = arrival - task
    completion; a small or negative lead is the bottleneck. Returns (cases,
    mean_lead_over_flagged, refs)."""
    tasks = {t.lower() for t in config.FOYLE_LEADTIME_TASKS}
    flagged: list[str] = []
    leads: list[float] = []
    refs: list[str] = []
    for case_id, g in df.groupby("case_id"):
        arr = g[g["stage"] == "arrival"]["ts"]
        if arr.empty or pd.isna(arr.iloc[0]):
            continue
        a = arr.iloc[0]
        worst: float | None = None
        worst_ref: str | None = None
        for _, row in g[g["stage"].isin(tasks)].iterrows():
            if pd.isna(row["ts"]):
                continue
            lead = (a - row["ts"]).days
            if lead < threshold_days and (worst is None or lead < worst):
                worst, worst_ref = lead, row["source_ref"]
        if worst is not None:
            flagged.append(case_id)
            leads.append(worst)
            if len(refs) < 3:
                refs.append(worst_ref)
    mean_lead = round(sum(leads) / len(leads), 1) if leads else 0.0
    return sorted(set(flagged)), mean_lead, refs


def _detect_tracker_open(df: pd.DataFrame, stages: set[str], done: set[str]):
    """Cohorts where any event in `stages` has a status outside `done` (still open).
    Used for both outstanding-critical-task and unpaid-deposit detection."""
    flagged: list[str] = []
    refs: list[str] = []
    for case_id, g in df.groupby("case_id"):
        for _, row in g[g["stage"].isin(stages)].iterrows():
            if _status_lower(row["status"]) not in done:
                flagged.append(case_id)
                if len(refs) < 3:
                    refs.append(row["source_ref"])
                break
    return sorted(set(flagged)), refs


def detect_foyle_tracker(df: pd.DataFrame | None = None) -> list[DetectedBottleneck]:
    """Detection for the single-sheet Foyle Placement Tracker. Three real patterns,
    all derivable from the tracker: milestone locked too close to arrival (delay),
    a critical task still open at arrival (outstanding), and an unpaid self-catering
    deposit (money)."""
    if df is None:
        df = load_event_log()

    done = {s.lower() for s in config.FOYLE_TRACKER_DONE_STATUSES}
    delay_cases, mean_lead, delay_refs = _detect_tracker_leadtime(df, config.FOYLE_LEAD_TIME_DAYS)
    crit_stages = {t.lower() for t in config.FOYLE_CRITICAL_TASKS}
    out_cases, out_refs = _detect_tracker_open(df, crit_stages, done)
    dep_cases, dep_refs = _detect_tracker_open(df, {config.FOYLE_DEPOSIT_TASK.lower()}, done)

    return [
        DetectedBottleneck(
            id="BN001", type="delay", stage="Accommodation Assigned",
            affected_cases=delay_cases, metric_label="avg_lead_days",
            metric_value=mean_lead, example_refs=delay_refs,
        ),
        DetectedBottleneck(
            id="BN002", type="outstanding", stage="Critical task",
            affected_cases=out_cases, metric_label="cohorts_with_open_task",
            metric_value=float(len(out_cases)), example_refs=out_refs,
        ),
        DetectedBottleneck(
            id="BN003", type="deposit", stage=config.FOYLE_DEPOSIT_TASK,
            affected_cases=dep_cases, metric_label="unpaid_deposit_cohorts",
            metric_value=float(len(dep_cases)), example_refs=dep_refs,
        ),
    ]


def detect_generic(df: pd.DataFrame, *, delay_stage: str, repetition_stage: str,
                   rework_stage: str, delay_threshold_days: int) -> list[DetectedBottleneck]:
    """The generic delay/repetition/rework detector, parametrised by marker
    stages so any profile (single-sheet model, messy-drive profiles) can run
    it with its own vocabulary. `detect_all` wraps it with the config defaults."""
    results: list[DetectedBottleneck] = []

    delay_cases, mean_gap, delay_refs = _detect_delay(
        df, delay_stage.lower(), delay_threshold_days
    )
    results.append(DetectedBottleneck(
        id="BN001", type="delay", stage=delay_stage,
        affected_cases=delay_cases, metric_label="avg_delay_days",
        metric_value=mean_gap, example_refs=delay_refs,
    ))

    rep_cases, rep_refs = _detect_presence(df, repetition_stage.lower())
    results.append(DetectedBottleneck(
        id="BN002", type="repetition", stage=repetition_stage,
        affected_cases=rep_cases, metric_label="duplicate_entry_cases",
        metric_value=float(len(rep_cases)), example_refs=rep_refs,
    ))

    rew_cases, rew_refs = _detect_presence(df, rework_stage.lower())
    results.append(DetectedBottleneck(
        id="BN003", type="rework", stage=rework_stage,
        affected_cases=rew_cases, metric_label="rework_loop_cases",
        metric_value=float(len(rew_cases)), example_refs=rew_refs,
    ))

    return results


def detect_all(df: pd.DataFrame | None = None) -> list[DetectedBottleneck]:
    if df is None:
        df = load_event_log()
    return detect_generic(
        df,
        delay_stage=config.DELAY_STAGE,
        repetition_stage=config.REPETITION_STAGE,
        rework_stage=config.REWORK_STAGE,
        delay_threshold_days=config.DELAY_THRESHOLD_DAYS,
    )
