"""Deterministic, explainable ranking of the action queue.

Two rules govern this module:

1. **Deterministic.** No randomness, no LLM, no wall-clock reads inside the
   score. The same queue always ranks the same way, and a test can assert it.
2. **Explainable.** Every point a item scores comes with a sentence saying
   where it came from. `rank_explanation` is shown in the UI next to the item,
   so a worker can disagree with the ordering on the merits.

The score is a weighted sum of normalised components, not an opaque model:

    money        how much revenue/cost is on the line, relative to the biggest
                 figure in this queue (so it never dominates a small SME)
    delivery     explicit client-delivery risk
    reach        how many cases are affected, relative to the queue's worst
    urgency      overdue > due today > due soon > undated
    confidence   detection confidence scales the whole thing DOWN — a shaky
                 finding should not outrank a certain one of similar size
"""

from __future__ import annotations

from datetime import date

from actions.models import ActionItem

WEIGHTS: dict[str, float] = {
    "money": 40.0,
    "delivery": 20.0,
    "reach": 20.0,
    "urgency": 20.0,
}

_DELIVERY_RISK_POINTS = {"high": 1.0, "medium": 0.55, "low": 0.2}

# Items already being worked sink below untouched ones — the queue is about
# what needs a decision now, not what is already in hand.
_STATUS_MULTIPLIER = {
    "proposed": 1.0, "approved": 0.9, "assigned": 0.6, "in_progress": 0.5,
    "completed": 0.2, "outcome_review": 0.3, "validated": 0.05,
    "ineffective": 0.05, "rejected": 0.0, "dismissed": 0.0,
}


def _urgency(item: ActionItem, today: date | None) -> tuple[float, str]:
    if not item.due_date:
        return 0.35, "no due date set (neutral urgency)"
    if today is None:
        return 0.35, "due date present but no reference date supplied"
    try:
        due = date.fromisoformat(item.due_date[:10])
    except ValueError:
        return 0.35, f"unparseable due date {item.due_date!r}"
    days = (due - today).days
    if days < 0:
        return 1.0, f"overdue by {abs(days)} day(s)"
    if days == 0:
        return 0.85, "due today"
    if days <= 3:
        return 0.65, f"due in {days} day(s)"
    if days <= 7:
        return 0.45, "due this week"
    return 0.25, f"due in {days} day(s)"


def score_item(item: ActionItem, *, max_money: float, max_reach: int,
               today: date | None = None) -> tuple[float, list[str]]:
    """Score one item against the queue's own maxima. Returns (score, why)."""
    why: list[str] = []
    total = 0.0

    money = item.impact.monetary_total
    if money > 0 and max_money > 0:
        share = min(1.0, money / max_money)
        points = WEIGHTS["money"] * share
        total += points
        label = "projected" if item.impact.is_projection else "measured"
        why.append(f"{item.impact.currency}{money:,.0f} {label} at risk "
                   f"({share:.0%} of the largest item) → {points:.1f} pts")
    else:
        why.append("no monetary figure available → 0 pts")

    if item.impact.delivery_risk:
        share = _DELIVERY_RISK_POINTS[item.impact.delivery_risk]
        points = WEIGHTS["delivery"] * share
        total += points
        why.append(f"{item.impact.delivery_risk} client-delivery risk "
                   f"→ {points:.1f} pts")

    reach = len(item.affected_case_ids)
    if reach and max_reach:
        share = min(1.0, reach / max_reach)
        points = WEIGHTS["reach"] * share
        total += points
        why.append(f"{reach} affected case(s), the queue's widest is "
                   f"{max_reach} → {points:.1f} pts")

    urgency_share, urgency_why = _urgency(item, today)
    urgency_points = WEIGHTS["urgency"] * urgency_share
    total += urgency_points
    why.append(f"{urgency_why} → {urgency_points:.1f} pts")

    confidence = item.detection_confidence
    if confidence is not None and confidence < 1.0:
        before = total
        total *= max(0.0, min(1.0, confidence))
        why.append(f"detection confidence {confidence:.0%} scales "
                   f"{before:.1f} → {total:.1f} pts")

    multiplier = _STATUS_MULTIPLIER.get(item.status, 1.0)
    if multiplier != 1.0:
        before = total
        total *= multiplier
        why.append(f"status '{item.status}' scales {before:.1f} → {total:.1f} pts")

    return round(total, 2), why


def rank(items: list[ActionItem], *, today: date | None = None) -> list[ActionItem]:
    """Score every item, write the score + explanation back onto it, and return
    the list ordered most-urgent-first.

    Ties break on affected-case count then action_id, so the ordering is total
    and stable — two runs over the same queue produce byte-identical output."""
    if not items:
        return []
    max_money = max((i.impact.monetary_total for i in items), default=0.0)
    max_reach = max((len(i.affected_case_ids) for i in items), default=0)

    for item in items:
        item.rank_score, item.rank_explanation = score_item(
            item, max_money=max_money, max_reach=max_reach, today=today)

    return sorted(items, key=lambda i: (-i.rank_score,
                                        -len(i.affected_case_ids),
                                        i.action_id))
