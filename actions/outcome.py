"""Did the intervention actually work?

The system takes a baseline measurement when a human approves an action, then
compares it against a LATER analysis of the same workflow. That comparison —
not the approval — is what decides whether a fix becomes trusted knowledge.

Three values are kept apart on purpose and labelled separately everywhere:

    baseline_value   measured, at approval time
    expected_value   PROJECTED, from the template's expected improvement
    observed_value   measured, at review time

`expected_value` is carried for display so a worker can see whether reality met
the promise, and is never an input to the effectiveness verdict.

The verdict itself has three states, not two. `effective=None` means "not
enough evidence yet" — too soon, or no comparable measurement — and is the
honest answer far more often than either True or False. A None never becomes
trusted knowledge, and never gets written off as a failure either.

Human validation is required on top: `effective` is the system's reading,
`validated_by_human` is somebody agreeing with it. Only both together open the
door to the resolution store.
"""

from __future__ import annotations

from datetime import datetime, timezone

from actions import lifecycle, store
from actions.models import AnalysisSnapshot, Intervention, InterventionOutcome

# How much a metric must move before the change counts as more than noise.
MATERIAL_CHANGE = 0.10   # 10%


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _direction(baseline: float, observed: float, lower_is_better: bool) -> str:
    if baseline == observed:
        return "unchanged"
    improved = observed < baseline if lower_is_better else observed > baseline
    return "improved" if improved else "worsened"


def compare(intervention: Intervention, snapshot: AnalysisSnapshot,
            *, at: str | None = None) -> InterventionOutcome:
    """Measure one intervention against one later snapshot.

    A finding that has disappeared from the analysis entirely is treated as
    fully resolved (observed = 0 for a lower-is-better metric), which is the
    strongest possible evidence the intervention worked — but it is still only
    a proposal until a human validates it."""
    baseline = intervention.baseline_value
    key = intervention.finding_key
    resolved = snapshot.is_resolved(key)
    observed = 0.0 if resolved else snapshot.metric_for(key)

    outcome = InterventionOutcome(
        measured_at=at or _now(),
        baseline_value=baseline,
        observed_value=observed,
        baseline_snapshot_id=intervention.baseline_snapshot_id,
        observed_snapshot_id=snapshot.snapshot_id,
    )

    if intervention.expected_improvement_pct is not None and baseline is not None:
        delta = baseline * (intervention.expected_improvement_pct / 100.0)
        outcome.expected_value = round(
            baseline - delta if intervention.improvement_is_decrease
            else baseline + delta, 2)

    if baseline is None or observed is None:
        outcome.effective = None
        outcome.effective_reason = (
            "No comparable measurement yet — the finding has no baseline or did "
            "not produce a metric in this analysis.")
        return outcome

    outcome.absolute_change = round(observed - baseline, 4)
    if baseline:
        outcome.percent_change = round(100.0 * (observed - baseline) / abs(baseline), 1)
    outcome.direction = _direction(baseline, observed,
                                   intervention.improvement_is_decrease)

    if resolved:
        outcome.effective = True
        outcome.effective_reason = (
            f"The finding is no longer detected. Baseline was {baseline:g} "
            f"{intervention.metric_label or ''}".strip() + ".")
        return outcome

    moved = abs(outcome.percent_change or 0.0) / 100.0
    if moved < MATERIAL_CHANGE:
        outcome.effective = None
        outcome.effective_reason = (
            f"Metric moved {outcome.percent_change:+.1f}%, inside the "
            f"{MATERIAL_CHANGE:.0%} noise band — too small to call either way.")
    elif outcome.direction == "improved":
        outcome.effective = True
        outcome.effective_reason = (
            f"{intervention.metric_label or 'metric'} moved "
            f"{baseline:g} → {observed:g} ({outcome.percent_change:+.1f}%).")
    else:
        outcome.effective = False
        outcome.effective_reason = (
            f"{intervention.metric_label or 'metric'} moved the wrong way: "
            f"{baseline:g} → {observed:g} ({outcome.percent_change:+.1f}%).")
    return outcome


def review(profile: str, snapshot: AnalysisSnapshot, *,
           interventions: list[Intervention] | None = None,
           path=None, actor: str = "system") -> list[Intervention]:
    """Attach a proposed outcome to every intervention ready to be judged.

    Only interventions a human has marked `completed` (or already in review)
    are measured — measuring work nobody has done yet would be scoring the
    weather. Nothing here validates anything; that needs a person."""
    items = interventions if interventions is not None \
        else store.load_interventions(profile, path)
    changed: list[Intervention] = []

    for intervention in items:
        if intervention.status not in ("completed", "outcome_review"):
            continue
        if intervention.baseline_snapshot_id == snapshot.snapshot_id:
            continue  # comparing a baseline against itself proves nothing
        intervention.outcome = compare(intervention, snapshot)
        if intervention.status == "completed":
            lifecycle.transition(intervention, "outcome_review", actor=actor,
                                 note="measured against a later analysis")
        else:
            intervention.updated_at = intervention.outcome.measured_at
        changed.append(intervention)

    if interventions is None and changed:
        store.save_interventions(profile, items, path)
    return changed


def validate(profile: str, intervention_id: str, *, effective: bool,
             validated_by: str = "human", note: str | None = None,
             path=None) -> Intervention:
    """A human confirms (or overrules) the system's reading.

    This is the only route to `validated`, and therefore the only route into
    the trusted resolution store. Overruling is allowed in both directions and
    is recorded as such — the audit trail keeps the disagreement."""
    items = store.load_interventions(profile, path)
    found = next((i for i in items if i.intervention_id == intervention_id), None)
    if found is None:
        raise KeyError(f"no intervention {intervention_id} for profile {profile}")

    stamp = _now()
    if found.outcome is None:
        found.outcome = InterventionOutcome(
            measured_at=stamp,
            effective_reason="Validated without an automated measurement.")

    overruled = (found.outcome.effective is not None
                 and found.outcome.effective is not effective)
    found.outcome.effective = effective
    found.outcome.validated_by_human = True
    found.outcome.validated_at = stamp
    found.outcome.validated_by = validated_by
    if overruled:
        found.outcome.effective_reason += (
            f" Human review overruled the automated reading "
            f"(marked {'effective' if effective else 'ineffective'}).")

    if found.status == "completed":
        lifecycle.transition(found, "outcome_review", actor=validated_by,
                             note="opened for validation")
    lifecycle.transition(found, "validated" if effective else "ineffective",
                         actor=validated_by, note=note, at=stamp)

    store.save_interventions(profile, items, path)
    return found


def summarise(profile: str, path=None) -> dict:
    """Counts for the dashboard's 'recently validated improvements' strip."""
    items = store.load_interventions(profile, path)
    by_status: dict[str, int] = {}
    for i in items:
        by_status[i.status] = by_status.get(i.status, 0) + 1
    validated = [i for i in items if i.is_trusted_knowledge]
    return {
        "profile": profile,
        "total": len(items),
        "by_status": by_status,
        "validated_effective": len(validated),
        "awaiting_review": by_status.get("outcome_review", 0),
        "ineffective": by_status.get("ineffective", 0),
    }
