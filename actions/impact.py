"""Turn a finding into a business-impact figure, with the arithmetic shown.

Two hard rules:

1. **Every number is explainable.** `BusinessImpact.basis` always spells out the
   sum that produced the figure ("4 cases × £4,200 average project fee"). A
   worker who disagrees with the ranking can see exactly which assumption to
   argue with.
2. **Projections are labelled.** A figure derived from the profile's cost
   assumptions is a projection (`is_projection=True`). A figure summed from
   money that is actually written in the SME's own spreadsheets is an
   observation (`is_projection=False`). The UI shows them differently and the
   outcome logic never treats one as the other.

All assumptions come from config: the existing `costs` block plus an optional
`actions.impact` block. A profile that defines neither still gets sensible,
clearly-projected numbers — it just cannot claim precision it hasn't earned.
"""

from __future__ import annotations

import config
from actions.models import BusinessImpact

# Used when a profile declares no impact assumptions at all. Chosen to be
# obviously generic rather than plausibly precise.
DEFAULT_IMPACT: dict = {
    "avg_case_value": None,
    "hourly_rate": 30.0,
    "hours_per_repetition": 2.0,
    "hours_per_rework": 6.0,
    "minutes_per_messy_cell": 0.5,
    "owner_load_limit": 5,
}


def assumptions(profile: str) -> dict:
    cfg = config.MESSY_PROFILES.get(profile, {})
    merged = {**DEFAULT_IMPACT, **((cfg.get("actions") or {}).get("impact") or {})}
    merged["costs"] = cfg.get("costs") or {}
    merged["currency"] = merged["costs"].get("currency", "£")
    return merged


def _case_values(case_details: list[dict]) -> tuple[list[float], int]:
    """(real values found, how many cases had none)."""
    values, missing = [], 0
    for d in case_details:
        v = d.get("value")
        if v is None:
            missing += 1
        else:
            values.append(float(v))
    return values, missing


def _money_at_risk(case_details: list[dict], affected: int,
                   a: dict) -> tuple[float | None, str, bool]:
    """Money on the line for a set of cases.

    Prefers the values written in the SME's own sheets; falls back to the
    profile's average case value for the cases that carry none. Returns
    (amount, basis, is_projection) — projection is False only when every single
    case brought its own real figure."""
    values, missing = _case_values(case_details)
    avg = a.get("avg_case_value")
    cur = a["currency"]

    if values and missing == 0:
        return (round(sum(values), 2),
                f"{len(values)} case(s) at their recorded value "
                f"({cur}{min(values):,.0f}–{cur}{max(values):,.0f})",
                False)
    if values and avg:
        total = sum(values) + missing * avg
        return (round(total, 2),
                f"{len(values)} case(s) at their recorded value plus "
                f"{missing} at the {cur}{avg:,.0f} average",
                True)
    if values:
        return (round(sum(values), 2),
                f"{len(values)} case(s) at their recorded value "
                f"({missing} case(s) carry no value)", True)
    if avg and affected:
        return (round(affected * avg, 2),
                f"{affected} case(s) × {cur}{avg:,.0f} average case value", True)
    return None, "", True


def _delivery_risk(days_over: float | None, affected: int) -> str | None:
    if days_over is None:
        return "medium" if affected >= 3 else "low"
    if days_over >= 14 or affected >= 5:
        return "high"
    if days_over >= 5 or affected >= 2:
        return "medium"
    return "low"


# ── Per finding type ─────────────────────────────────────────────────────────

def _structural_impact(finding_type: str, affected: int, metric: float,
                       a: dict) -> BusinessImpact:
    """delay / repetition / rework — the process-shaped findings. These cost
    staff time and drag delivery; they are always projections from the
    profile's cost model."""
    costs, cur = a["costs"], a["currency"]
    if finding_type == "delay":
        rate = costs.get("delay_day_cost")
        amount = round(affected * metric * rate, 2) if rate else None
        basis = (f"{affected} case(s) × {metric:.0f} days × {cur}{rate}/day"
                 if rate else f"{affected} case(s) delayed {metric:.0f} days on average")
        return BusinessImpact(
            category="time_loss", currency=cur, cost_incurred=amount,
            hours_at_risk=None, delivery_risk=_delivery_risk(metric, affected),
            affected_case_count=affected, basis=basis, is_projection=True)

    if finding_type == "repetition":
        rate = costs.get("repetition_event_cost")
        hours = affected * a.get("hours_per_repetition", 0)
        amount = round(affected * rate, 2) if rate else None
        basis = (f"{affected} repeated step(s) × {cur}{rate}"
                 if rate else f"{affected} repeated step(s)")
        return BusinessImpact(
            category="time_loss", currency=cur, cost_incurred=amount,
            hours_at_risk=round(hours, 1) or None,
            delivery_risk=_delivery_risk(None, affected),
            affected_case_count=affected, basis=basis, is_projection=True)

    # rework
    rate = costs.get("rework_loop_cost")
    hours = affected * a.get("hours_per_rework", 0)
    amount = round(affected * rate, 2) if rate else None
    basis = (f"{affected} loop-back(s) × {cur}{rate}"
             if rate else f"{affected} loop-back(s)")
    return BusinessImpact(
        category="delivery_risk", currency=cur, cost_incurred=amount,
        hours_at_risk=round(hours, 1) or None,
        delivery_risk=_delivery_risk(None, affected),
        affected_case_count=affected, basis=basis, is_projection=True)


def _case_rule_impact(finding_type: str, affected: int, metric: float,
                      case_details: list[dict], a: dict) -> BusinessImpact:
    cur = a["currency"]

    if finding_type == "unrealised_value":
        values, missing = _case_values(case_details)
        total = round(sum(values), 2) if values else None
        return BusinessImpact(
            category="revenue_at_risk", currency=cur, revenue_at_risk=total,
            delivery_risk=_delivery_risk(None, affected),
            affected_case_count=affected,
            basis=(f"{len(values)} case(s) summed at their recorded value"
                   if values else f"{affected} case(s), no recorded values"),
            is_projection=bool(missing) or not values)

    if finding_type in ("stage_sla_breach", "stalled_case", "unowned_case"):
        amount, basis, projected = _money_at_risk(case_details, affected, a)
        days_over = max((d.get("days_over") or d.get("days_idle") or 0
                         for d in case_details), default=None)
        return BusinessImpact(
            category="revenue_at_risk" if amount else "delivery_risk",
            currency=cur, revenue_at_risk=amount,
            delivery_risk=("high" if finding_type == "unowned_case"
                           else _delivery_risk(days_over, affected)),
            affected_case_count=affected, basis=basis or f"{affected} case(s) affected",
            is_projection=projected)

    if finding_type == "overloaded_owner":
        limit = a.get("owner_load_limit") or 5
        pct = round(100.0 * metric / limit, 1) if limit else None
        over = max(0.0, metric - limit)
        hours = round(over * a.get("hours_per_repetition", 2.0), 1)
        return BusinessImpact(
            category="capacity_loss", currency=cur, hours_at_risk=hours or None,
            capacity_pct=pct, delivery_risk=_delivery_risk(None, affected),
            affected_case_count=affected,
            basis=(f"{metric:.0f} open case(s) against a working limit of "
                   f"{limit} — {over:.0f} over"),
            is_projection=True)

    if finding_type == "key_person_dependency":
        return BusinessImpact(
            category="capacity_loss", currency=cur,
            capacity_pct=round(metric * 100, 1), delivery_risk="high",
            affected_case_count=affected,
            basis=f"one person handles {metric:.0%} of the work at this stage",
            is_projection=True)

    # anomaly and anything else unrecognised: no invented numbers.
    return BusinessImpact(category="unknown", currency=cur,
                          affected_case_count=affected,
                          basis="not quantified", is_projection=True)


def data_quality_impact(messy_cells: int, a: dict) -> BusinessImpact:
    """Messy data costs the time spent reading round it, not lost revenue."""
    minutes = messy_cells * a.get("minutes_per_messy_cell", 0.5)
    hours = round(minutes / 60.0, 1)
    rate = a.get("hourly_rate") or 0
    return BusinessImpact(
        category="data_quality", currency=a["currency"],
        hours_at_risk=hours or None,
        cost_incurred=round(hours * rate, 2) if rate and hours else None,
        affected_case_count=0,
        basis=(f"{messy_cells} inconsistent cell(s) × "
               f"{a.get('minutes_per_messy_cell', 0.5)} min to interpret"),
        is_projection=True)


def build_impact(profile: str, finding_type: str, *, affected: int,
                 metric: float, case_details: list[dict] | None = None
                 ) -> BusinessImpact:
    """The single entry point. Deterministic for a given finding + config."""
    a = assumptions(profile)
    case_details = case_details or []
    if finding_type in ("delay", "repetition", "rework"):
        return _structural_impact(finding_type, affected, metric, a)
    return _case_rule_impact(finding_type, affected, metric, case_details, a)
