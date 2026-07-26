"""The intervention lifecycle — the state machine, and the only door into
trusted organisational knowledge.

    proposed ─┬─> approved -> assigned -> in_progress -> completed
              │                                             │
              │                                             v
              │                                      outcome_review
              │                                       │          │
              │                                       v          v
              │                                  validated   ineffective
              ├─> rejected      (human said no)
              └─> dismissed     (human said "not relevant to us")

Why this exists: the previous build treated *approval* as proof a fix worked
and wrote it straight into the RAG resolution store. Approval is a decision to
try something, not evidence it helped. Only an intervention that reaches
`validated` — with an outcome measured against a later analysis AND a human
confirming that reading — is trusted guidance.

Rejected, dismissed and ineffective interventions keep their full history.
They are auditable, queryable, and deliberately never retrievable as advice.
"""

from __future__ import annotations

from datetime import datetime, timezone

from actions.models import ActionStatus, Intervention, LifecycleEvent

# to_status -> the statuses it may be entered from.
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": (),
    "approved": ("proposed",),
    "assigned": ("approved", "assigned"),
    "in_progress": ("approved", "assigned", "in_progress"),
    "completed": ("assigned", "in_progress", "completed"),
    "outcome_review": ("completed", "outcome_review"),
    # A review can be re-run: an intervention judged ineffective at one review
    # may be re-measured later, so both terminals can return to review.
    "validated": ("outcome_review",),
    "ineffective": ("outcome_review",),
    # Off-ramps. Reachable from anywhere before an outcome verdict exists.
    "rejected": ("proposed", "approved", "assigned", "in_progress"),
    "dismissed": ("proposed", "approved", "assigned", "in_progress",
                  "completed", "outcome_review"),
}

# Terminal verdicts may be re-opened for a fresh measurement, but only back
# into review — never straight back into "approved".
REOPENABLE = {"validated": ("outcome_review",), "ineffective": ("outcome_review",)}

TERMINAL: frozenset[str] = frozenset({"validated", "ineffective", "rejected",
                                      "dismissed"})


class TransitionError(ValueError):
    """An illegal lifecycle move. Raised rather than silently corrected — a
    bad transition means a caller misunderstood the model."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def can_transition(from_status: str, to_status: str) -> bool:
    if to_status not in ALLOWED_TRANSITIONS:
        return False
    if from_status in REOPENABLE and to_status in REOPENABLE[from_status]:
        return True
    return from_status in ALLOWED_TRANSITIONS[to_status]


def transition(intervention: Intervention, to_status: ActionStatus, *,
               actor: str = "human", note: str | None = None,
               at: str | None = None) -> Intervention:
    """Move an intervention, appending to its permanent history.

    Idempotent on a repeated identical status only where the state machine
    allows self-transitions (assigned/in_progress/completed/outcome_review) —
    re-assigning to a new owner is a real event worth logging."""
    if not can_transition(intervention.status, to_status):
        raise TransitionError(
            f"{intervention.intervention_id}: cannot go "
            f"{intervention.status!r} -> {to_status!r}")
    stamp = at or _now()
    intervention.history.append(LifecycleEvent(
        at=stamp, from_status=intervention.status, to_status=to_status,
        actor=actor, note=note))
    intervention.status = to_status
    intervention.updated_at = stamp
    return intervention


def is_trusted(intervention: Intervention) -> bool:
    """The single predicate the learning loop consults. Kept as a function as
    well as a property so tests and the learn module read identically."""
    return intervention.is_trusted_knowledge
