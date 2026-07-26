"""Case-level detection — which individual cases need attention, and why.

`detection/dynamic.py` answers "which STAGES are structurally broken". That is
the right question for a process analyst and the wrong one for a worker on a
Tuesday morning, who needs to know which *lead*, *job* or *booking* to touch.
This module produces that second kind of finding.

Six rules, all generic. Not one of them knows what an SME sells — the
vocabulary comes entirely from `config.MESSY_PROFILES[<p>]["case_rules"]`:

    stage_sla_breach      a case has sat at a stage past its agreed SLA
    stalled_case          a case has not moved at all for too long and is not finished
    unowned_case          a case whose latest activity has nobody's name on it
    unrealised_value      a case carrying money that has not reached a revenue stage
    overloaded_owner      one person holding more open cases than the load limit
    key_person_dependency one person doing nearly all the work at a stage

Between them these cover the operational patterns SMEs actually complain about
— uncontacted leads, proposals stuck in internal approval, delivered work not
invoiced, overdue invoices, jobs with nobody's name on them, and the one
specialist everything queues behind — without a line of per-SME code.

`as_of` defaults to the newest timestamp in the log rather than the wall clock.
On synthetic data that keeps every run reproducible; against a live drive the
newest event IS effectively now. Callers wanting true wall-clock ageing pass it.

No PII is read: `actor` values are placeholders by the time an event log
exists, and the rules only ever count and compare them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Defaults for a profile that declares no case_rules block at all. Deliberately
# loose — a profile should opt in to tighter SLAs, not be surprised by them.
DEFAULT_RULES: dict = {
    "stage_sla_days": {},
    "default_stalled_days": 21,
    "terminal_stages": [],
    "revenue_stages": [],
    "owner_load_limit": 5,
    "key_person_min_share": 0.6,
    "key_person_min_events": 5,
    "min_affected": 1,
}

# Rules that describe a PERSON rather than a case. They still produce action
# items, but their "affected cases" are the cases that person is holding.
_PEOPLE_RULES = ("overloaded_owner", "key_person_dependency")


@dataclass
class CaseFinding:
    """One case-level finding. Mirrors DetectedBottleneck's vocabulary where it
    overlaps so the action builder can treat both uniformly, and adds
    `case_details` — the per-case numbers a worker needs to act."""

    id: str
    type: str
    stage: str
    affected_cases: list[str]
    metric_label: str
    metric_value: float
    title: str = ""
    detail: str = ""
    case_details: list[dict] = field(default_factory=list)
    example_refs: list[str] = field(default_factory=list)
    subject: str | None = None          # the actor, for the people-shaped rules

    @property
    def affected_count(self) -> int:
        return len(self.affected_cases)


def rules_for(profile_cfg: dict) -> dict:
    return {**DEFAULT_RULES, **(profile_cfg.get("case_rules") or {})}


def _canon(label: str) -> str:
    return str(label).strip().lower()


def _label_map(stage_order: list[str]) -> dict[str, str]:
    return {_canon(s): s for s in stage_order}


def analysis_date(df: pd.DataFrame, as_of=None) -> pd.Timestamp | None:
    """The reference 'now'. Newest event in the log unless told otherwise."""
    if as_of is not None:
        return pd.Timestamp(as_of)
    ts = pd.to_datetime(df["ts"], errors="coerce").dropna()
    return ts.max() if not ts.empty else None


def _case_state(df: pd.DataFrame, stage_order: list[str]) -> pd.DataFrame:
    """One row per case: its latest event, current stage, owner and value."""
    order = {_canon(s): i for i, s in enumerate(stage_order)}
    rows = []
    for case_id, g in df.sort_values("ts").groupby("case_id"):
        g = g.reset_index(drop=True)
        last = g.iloc[-1]
        stages = [s for s in g["stage"] if s in order]
        furthest = max((order[s] for s in stages), default=-1)
        value = pd.to_numeric(g.get("value"), errors="coerce").dropna() \
            if "value" in g.columns else pd.Series(dtype=float)
        rows.append({
            "case_id": case_id,
            "last_ts": last["ts"],
            "last_stage": last["stage"],
            "furthest_index": furthest,
            "actor": last.get("actor"),
            "source_ref": last.get("source_ref"),
            "value": float(value.max()) if not value.empty else None,
            "stages": set(g["stage"]),
        })
    return pd.DataFrame(rows)


def _is_finished(state_row, terminal_canon: set[str]) -> bool:
    return bool(terminal_canon and (state_row["stages"] & terminal_canon))


# ── The rules ────────────────────────────────────────────────────────────────

def _stage_sla_breaches(state: pd.DataFrame, rules: dict, labels: dict,
                        now: pd.Timestamp) -> list[CaseFinding]:
    """A case is sitting at a stage longer than that stage's agreed SLA.

    Grouped BY STAGE, not per case, so the worker gets one queue item ("4 leads
    have gone more than 3 working days without contact") that expands into the
    individual cases — rather than four separate rows saying the same thing."""
    sla = {_canon(k): v for k, v in (rules.get("stage_sla_days") or {}).items()}
    if not sla or now is None:
        return []
    terminal = {_canon(s) for s in rules.get("terminal_stages") or []}
    by_stage: dict[str, list[dict]] = {}

    for _, row in state.iterrows():
        stage = row["last_stage"]
        limit = sla.get(stage)
        if limit is None or _is_finished(row, terminal):
            continue
        if pd.isna(row["last_ts"]):
            continue
        waiting = int((now - row["last_ts"]).days)
        if waiting <= limit:
            continue
        by_stage.setdefault(stage, []).append({
            "case_id": row["case_id"], "days_waiting": waiting,
            "sla_days": int(limit), "days_over": waiting - int(limit),
            "owner": row["actor"], "value": row["value"],
            "source_ref": row["source_ref"],
        })

    out: list[CaseFinding] = []
    for stage, details in by_stage.items():
        if len(details) < rules.get("min_affected", 1):
            continue
        details.sort(key=lambda d: -d["days_waiting"])
        label = labels.get(stage, stage.title())
        worst = details[0]["days_waiting"]
        out.append(CaseFinding(
            id="", type="stage_sla_breach", stage=label,
            affected_cases=sorted(d["case_id"] for d in details),
            metric_label="days_over_sla",
            metric_value=float(round(sum(d["days_over"] for d in details)
                                     / len(details), 1)),
            title=f"{len(details)} case(s) past the {label} SLA",
            detail=(f"The agreed limit at {label} is "
                    f"{details[0]['sla_days']} days; the worst has waited {worst}."),
            case_details=details,
            example_refs=[d["source_ref"] for d in details[:3] if d["source_ref"]],
        ))
    return out


def _stalled_cases(state: pd.DataFrame, rules: dict, labels: dict,
                   now: pd.Timestamp) -> list[CaseFinding]:
    """Nothing has happened on this case for a long time and it is not finished.

    Deliberately separate from the SLA rule: an SLA says "this stage should
    take N days", a stall says "this case has fallen off everyone's radar"."""
    limit = rules.get("default_stalled_days")
    if not limit or now is None:
        return []
    sla = {_canon(k) for k in (rules.get("stage_sla_days") or {})}
    terminal = {_canon(s) for s in rules.get("terminal_stages") or []}
    details: list[dict] = []

    for _, row in state.iterrows():
        if _is_finished(row, terminal) or pd.isna(row["last_ts"]):
            continue
        idle = int((now - row["last_ts"]).days)
        if idle <= limit:
            continue
        # Already reported by the tighter, more specific SLA rule — one finding
        # per problem, or the queue reads like a duplicate log.
        if row["last_stage"] in sla:
            continue
        details.append({
            "case_id": row["case_id"], "days_idle": idle,
            "stage": labels.get(row["last_stage"], row["last_stage"].title()),
            "owner": row["actor"], "value": row["value"],
            "source_ref": row["source_ref"],
        })

    if len(details) < rules.get("min_affected", 1):
        return []
    details.sort(key=lambda d: -d["days_idle"])
    return [CaseFinding(
        id="", type="stalled_case", stage="",
        affected_cases=sorted(d["case_id"] for d in details),
        metric_label="days_idle",
        metric_value=float(round(sum(d["days_idle"] for d in details) / len(details), 1)),
        title=f"{len(details)} case(s) have gone quiet",
        detail=(f"No activity for over {limit} days and no completion recorded. "
                f"The longest has been idle {details[0]['days_idle']} days."),
        case_details=details,
        example_refs=[d["source_ref"] for d in details[:3] if d["source_ref"]],
    )]


def _unowned_cases(state: pd.DataFrame, rules: dict, labels: dict,
                   now: pd.Timestamp) -> list[CaseFinding]:
    """Nobody's name is against the case's latest activity."""
    terminal = {_canon(s) for s in rules.get("terminal_stages") or []}
    details = [{
        "case_id": row["case_id"],
        "stage": labels.get(row["last_stage"], row["last_stage"].title()),
        "value": row["value"], "source_ref": row["source_ref"],
    } for _, row in state.iterrows()
        if not _is_finished(row, terminal)
        and (row["actor"] is None or not str(row["actor"]).strip())]

    if len(details) < rules.get("min_affected", 1):
        return []
    return [CaseFinding(
        id="", type="unowned_case", stage="",
        affected_cases=sorted(d["case_id"] for d in details),
        metric_label="unowned_cases", metric_value=float(len(details)),
        title=f"{len(details)} open case(s) have no owner",
        detail="Work with nobody's name against it is work nobody is doing.",
        case_details=details,
        example_refs=[d["source_ref"] for d in details[:3] if d["source_ref"]],
    )]


def _unrealised_value(state: pd.DataFrame, rules: dict, labels: dict,
                      now: pd.Timestamp, currency: str = "") -> list[CaseFinding]:
    """Cases carrying money that have not reached a revenue stage.

    This is the rule that turns the queue commercial: it is the difference
    between "3 cases are slow" and "£18,400 is sitting unbilled"."""
    revenue = {_canon(s) for s in rules.get("revenue_stages") or []}
    if not revenue or now is None:
        return []
    terminal = {_canon(s) for s in rules.get("terminal_stages") or []}
    details = []
    for _, row in state.iterrows():
        if row["value"] is None or row["stages"] & revenue:
            continue
        if _is_finished(row, terminal) or pd.isna(row["last_ts"]):
            continue
        details.append({
            "case_id": row["case_id"], "value": row["value"],
            "days_waiting": int((now - row["last_ts"]).days),
            "stage": labels.get(row["last_stage"], row["last_stage"].title()),
            "owner": row["actor"], "source_ref": row["source_ref"],
        })

    if len(details) < rules.get("min_affected", 1):
        return []
    details.sort(key=lambda d: -(d["value"] or 0))
    total = sum(d["value"] or 0 for d in details)
    return [CaseFinding(
        id="", type="unrealised_value", stage="",
        affected_cases=sorted(d["case_id"] for d in details),
        metric_label="value_not_yet_realised", metric_value=float(round(total, 2)),
        title=(f"{len(details)} case(s) worth {currency}{total:,.0f} "
               f"not yet billed"),
        detail=("These cases carry a value but have not reached a revenue "
                "stage. Every day they sit there is cash not in the bank."),
        case_details=details,
        example_refs=[d["source_ref"] for d in details[:3] if d["source_ref"]],
    )]


def _overloaded_owners(state: pd.DataFrame, rules: dict, labels: dict,
                       now: pd.Timestamp) -> list[CaseFinding]:
    """One person is holding more open cases than the profile's load limit."""
    limit = rules.get("owner_load_limit")
    if not limit:
        return []
    terminal = {_canon(s) for s in rules.get("terminal_stages") or []}
    holding: dict[str, list[dict]] = {}
    for _, row in state.iterrows():
        actor = (row["actor"] or "").strip()
        if not actor or _is_finished(row, terminal):
            continue
        holding.setdefault(actor, []).append({
            "case_id": row["case_id"],
            "stage": labels.get(row["last_stage"], row["last_stage"].title()),
            "value": row["value"], "source_ref": row["source_ref"],
        })

    out: list[CaseFinding] = []
    for actor, details in sorted(holding.items()):
        if len(details) <= limit:
            continue
        out.append(CaseFinding(
            id="", type="overloaded_owner", stage="",
            affected_cases=sorted(d["case_id"] for d in details),
            metric_label="open_cases_held", metric_value=float(len(details)),
            title=f"{actor} is holding {len(details)} open cases",
            detail=(f"The working limit for this business is {limit}. "
                    f"Everything above it queues behind one person."),
            case_details=details, subject=actor,
            example_refs=[d["source_ref"] for d in details[:3] if d["source_ref"]],
        ))
    return out


def _key_person_dependency(df: pd.DataFrame, rules: dict, labels: dict,
                           now: pd.Timestamp) -> list[CaseFinding]:
    """Nearly all the work at one stage runs through a single person.

    A capacity risk that is invisible until that person is off: measured over
    events at the stage, not over cases, because that is where the hours go."""
    share_limit = rules.get("key_person_min_share")
    min_events = rules.get("key_person_min_events", 5)
    if not share_limit:
        return []
    out: list[CaseFinding] = []
    for stage, g in df.groupby("stage"):
        actors = g["actor"].dropna().astype(str).str.strip()
        actors = actors[actors != ""]
        if len(actors) < min_events or actors.nunique() < 2:
            continue
        counts = actors.value_counts()
        top_actor, top_count = counts.index[0], int(counts.iloc[0])
        share = top_count / len(actors)
        if share < share_limit:
            continue
        cases = sorted(set(g[g["actor"].astype(str).str.strip() == top_actor]["case_id"]))
        label = labels.get(stage, str(stage).title())
        out.append(CaseFinding(
            id="", type="key_person_dependency", stage=label,
            affected_cases=cases,
            metric_label="share_of_stage_events", metric_value=round(share, 2),
            title=f"{label} depends almost entirely on {top_actor}",
            detail=(f"{top_actor} handled {top_count} of {len(actors)} "
                    f"{label} events ({share:.0%}). If they are unavailable, "
                    f"this stage stops."),
            case_details=[{"case_id": c, "stage": label} for c in cases],
            subject=top_actor,
            example_refs=g["source_ref"].dropna().head(3).tolist(),
        ))
    return out


# ── Entry point ──────────────────────────────────────────────────────────────

_RULES = (
    ("stage_sla_breach", _stage_sla_breaches),
    ("stalled_case", _stalled_cases),
    ("unowned_case", _unowned_cases),
    ("overloaded_owner", _overloaded_owners),
)


def detect_case_findings(df: pd.DataFrame, profile_cfg: dict,
                         *, as_of=None) -> list[CaseFinding]:
    """Run every case-level rule over the event log. 0..N findings, ordered by
    reach then metric, with stable CF001.. ids."""
    rules = rules_for(profile_cfg)
    stage_order = profile_cfg.get("stage_order", [])
    labels = _label_map(stage_order)
    now = analysis_date(df, as_of)
    if df.empty or now is None:
        return []

    state = _case_state(df, stage_order)
    currency = (profile_cfg.get("costs") or {}).get("currency", "")
    found: list[CaseFinding] = []
    for _, fn in _RULES:
        found.extend(fn(state, rules, labels, now))
    found.extend(_unrealised_value(state, rules, labels, now, currency))
    found.extend(_key_person_dependency(df, rules, labels, now))

    found.sort(key=lambda f: (-f.affected_count, -f.metric_value, f.type, f.stage))
    for i, finding in enumerate(found, start=1):
        finding.id = f"CF{i:03d}"
    return found
