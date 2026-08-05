"""Domain models for the action layer — the worker-facing contract.

Six shapes, none of them SME-specific:

    EvidenceReference    a pointer back into the source data (row, metric or excerpt)
    BusinessImpact       what the finding costs, with the arithmetic spelled out
    ActionItem           one row of the action queue
    Intervention         the tracked commitment created when an item is approved
    InterventionOutcome  measured baseline -> observed comparison, plus human validation
    AnalysisSnapshot     the metric state of one analysis run, so later runs can compare

The lifecycle is shared by ActionItem and Intervention:

    proposed -> approved -> assigned -> in_progress -> completed
             -> outcome_review -> validated | ineffective

with `rejected` and `dismissed` as terminal off-ramps from the front of the
chain. Approval alone is NOT evidence a fix worked — only `validated` (with an
outcome that shows improvement) earns a place in the trusted resolution store.
See `actions/lifecycle.py` for the permitted transitions.

Projections and observations are deliberately different fields everywhere they
meet: `BusinessImpact.is_projection`, `Intervention.expected_improvement` vs
`InterventionOutcome.observed_value`. The UI labels them differently and the
outcome logic never compares a projection against a projection.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Optional

from pydantic import BaseModel, Field

# ── Closed vocabularies ──────────────────────────────────────────────────────

# WHAT KIND of work an item is. This is the routing decision that stops an
# approved "chase the overdue invoice" from launching a spreadsheet cleaner.
#   data_quality        — the data itself is wrong/messy. Some of these are
#                         machine-executable after approval (the remediation
#                         executor's cleaned-copy flow).
#   case_action         — a human must do something to a specific case
#                         (follow up, chase, assign, invoice). NEVER machine-executed.
#   process_intervention— a change to how the work is run (an SLA, a WIP limit,
#                         a checkpoint). Becomes a measurable experiment.
ActionCategory = Literal["data_quality", "case_action", "process_intervention"]

ACTION_CATEGORIES: tuple[str, ...] = (
    "data_quality", "case_action", "process_intervention")

# Only these data-quality templates may be executed by machine after approval.
# Anything else — including every case_action and process_intervention — is
# tracked work for a human. Kept here (not in the executor) so the guarantee is
# visible from the model layer that the API and UI both read.
MACHINE_EXECUTABLE_TEMPLATES: tuple[str, ...] = ("normalise_status_values",)

ActionStatus = Literal[
    "proposed", "approved", "assigned", "in_progress", "completed",
    "outcome_review", "validated", "ineffective", "rejected", "dismissed",
]

# Where the impact lands. Used for the queue's section headings and for
# ranking; the numbers themselves live in the sibling fields.
ImpactCategory = Literal[
    "revenue_at_risk", "delivery_risk", "capacity_loss", "time_loss",
    "data_quality", "unknown",
]

EvidenceKind = Literal["source_row", "metric", "excerpt", "case_list"]


# ── Evidence ─────────────────────────────────────────────────────────────────

class EvidenceReference(BaseModel):
    """One traceable reason the system flagged something.

    `source_ref` is the pipeline's own "<file>:<sheet>:<row>" pointer, so a
    worker can open the actual spreadsheet row. Free-text `excerpt` values are
    scrubbed upstream — this model never re-introduces raw PII."""

    kind: EvidenceKind
    label: str                              # human-readable, e.g. "Gap into Proposal"
    detail: Optional[str] = None            # the plain-language explanation
    source_ref: Optional[str] = None        # "<file>:<sheet>:<row>"
    source_file: Optional[str] = None
    sheet: Optional[str] = None
    row: Optional[int] = None
    case_id: Optional[str] = None
    metric_label: Optional[str] = None
    metric_value: Optional[float] = None
    excerpt: Optional[str] = None           # scrubbed text only

    @classmethod
    def from_source_ref(cls, source_ref: str, *, label: str,
                        case_id: str | None = None,
                        detail: str | None = None) -> "EvidenceReference":
        """Split "<file>:<sheet>:<row>" into its parts so the UI can deep-link
        the spreadsheet without re-parsing strings."""
        parts = (source_ref or "").split(":")
        row: int | None = None
        if len(parts) >= 3 and parts[-1].isdigit():
            row = int(parts[-1])
        return cls(
            kind="source_row", label=label, detail=detail, source_ref=source_ref,
            source_file=parts[0] if parts and parts[0] else None,
            sheet=parts[1] if len(parts) >= 3 else None,
            row=row, case_id=case_id,
        )


# ── Impact ───────────────────────────────────────────────────────────────────

class BusinessImpact(BaseModel):
    """What the finding is costing, and how that number was reached.

    Every populated figure is a PROJECTION from the profile's cost assumptions
    unless `is_projection` is False — the dashboard must label it as such. The
    `basis` string is the arithmetic in words and is what makes the ranking
    explainable rather than an opaque score."""

    category: ImpactCategory = "unknown"
    currency: str = "£"
    revenue_at_risk: Optional[float] = None    # money that may not land / lands late
    cost_incurred: Optional[float] = None      # money already burnt (rework, delay)
    hours_at_risk: Optional[float] = None      # staff time lost or at risk
    capacity_pct: Optional[float] = None       # 0-100, how loaded a constrained resource is
    delivery_risk: Optional[Literal["low", "medium", "high"]] = None
    affected_case_count: int = 0
    basis: str = ""                            # "6 leads × £4,200 avg deal value"
    is_projection: bool = True

    @property
    def monetary_total(self) -> float:
        """Single money figure for ranking/summing. Revenue at risk and cost
        already incurred are different things but both are money on the line."""
        return float(self.revenue_at_risk or 0.0) + float(self.cost_incurred or 0.0)


# ── The queue row ────────────────────────────────────────────────────────────

class ActionItem(BaseModel):
    """One thing that needs attention, with everything a worker needs to decide.

    `finding_key` is stable across analyses (type + stage + metric), which is
    what lets a later snapshot find the same finding and measure whether an
    intervention moved it. `action_id` is derived from it so re-running the
    analysis updates an item rather than duplicating it."""

    action_id: str
    profile: str
    finding_key: str
    finding_type: str                       # delay | repetition | rework | anomaly | <case rule>
    title: str                              # plain language, no jargon
    summary: str = ""
    workflow: str = ""                      # the SME's workflow name (from profile ui)
    stage: str = ""
    affected_case_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    # The RAG grounding behind this recommendation: past resolutions the
    # diagnosis agent retrieved, with similarity scores. Empty when the item
    # came from a case rule or the diagnosis was offline.
    retrieved_resolutions: list[dict] = Field(default_factory=list)
    metric_label: Optional[str] = None
    metric_value: Optional[float] = None
    detection_confidence: Optional[float] = None   # how sure the detector is
    data_quality_confidence: Optional[float] = None  # how trustworthy the underlying data is
    impact: BusinessImpact = Field(default_factory=BusinessImpact)
    recommended_action: str = ""
    action_steps: list[str] = Field(default_factory=list)
    action_category: ActionCategory = "case_action"
    action_template: Optional[str] = None   # set for machine-executable data fixes
    owner: Optional[str] = None
    due_date: Optional[str] = None          # ISO date
    status: ActionStatus = "proposed"
    created_at: str = ""
    updated_at: str = ""
    source_records: list[str] = Field(default_factory=list)  # source_refs
    intervention_id: Optional[str] = None
    rank_score: float = 0.0
    rank_explanation: list[str] = Field(default_factory=list)
    llm_payload: Optional[dict] = None      # exactly what was sent to an LLM, or None
    generated_by: Literal["detector", "case_rule"] = "detector"

    @property
    def is_machine_executable(self) -> bool:
        """The single gate on automated execution: data-quality category AND a
        template on the machine-safe allow-list. Everything else is human work."""
        return (self.action_category == "data_quality"
                and self.action_template in MACHINE_EXECUTABLE_TEMPLATES)


def make_action_id(profile: str, finding_key: str) -> str:
    """Deterministic id: the same finding in the same profile always gets the
    same id, so re-analysis updates in place instead of piling up duplicates."""
    digest = hashlib.sha1(f"{profile}|{finding_key}".encode("utf-8")).hexdigest()
    return f"ACT-{profile[:3].upper()}-{digest[:8]}"


# ── Interventions and their outcomes ─────────────────────────────────────────

class LifecycleEvent(BaseModel):
    """One transition, kept forever. Rejected and ineffective interventions
    keep their history — the audit trail is the point — they just never become
    trusted guidance."""

    at: str
    from_status: Optional[ActionStatus] = None
    to_status: ActionStatus
    actor: str = "human"
    note: Optional[str] = None


class InterventionOutcome(BaseModel):
    """Did it work? Baseline and observation are both real measurements taken
    from AnalysisSnapshots; `expected_value` is the projection made at approval
    time and is never used to decide effectiveness."""

    measured_at: str
    baseline_value: Optional[float] = None
    expected_value: Optional[float] = None      # projection, for display only
    observed_value: Optional[float] = None
    absolute_change: Optional[float] = None
    percent_change: Optional[float] = None
    direction: Literal["improved", "worsened", "unchanged", "unknown"] = "unknown"
    effective: Optional[bool] = None            # None = not enough evidence yet
    effective_reason: str = ""
    validated_by_human: bool = False
    validated_at: Optional[str] = None
    validated_by: Optional[str] = None
    baseline_snapshot_id: Optional[str] = None
    observed_snapshot_id: Optional[str] = None


class Intervention(BaseModel):
    """The commitment created when a human approves an action item.

    Holds the baseline measurement taken at approval, the success metric, who
    owns it and when it will be reviewed. Only an intervention that reaches
    `validated` with `outcome.effective is True` may enter the trusted
    resolution store (see pipeline/learn.py)."""

    intervention_id: str
    action_id: str
    profile: str
    finding_key: str
    action_category: ActionCategory
    title: str
    recommended_action: str = ""
    action_steps: list[str] = Field(default_factory=list)
    status: ActionStatus = "approved"
    owner: Optional[str] = None
    due_date: Optional[str] = None
    review_date: Optional[str] = None
    success_metric: str = ""                    # "avg_delay_days falls below 7"
    metric_label: Optional[str] = None
    baseline_value: Optional[float] = None
    baseline_snapshot_id: Optional[str] = None
    expected_improvement_pct: Optional[float] = None   # projection at approval
    improvement_is_decrease: bool = True         # lower metric = better (the usual case)
    created_at: str = ""
    updated_at: str = ""
    outcome: Optional[InterventionOutcome] = None
    history: list[LifecycleEvent] = Field(default_factory=list)
    decision_id: Optional[str] = None            # links back to the Gate-2 decision
    case_snapshot: dict = Field(default_factory=dict)  # what the finding looked like

    @property
    def is_trusted_knowledge(self) -> bool:
        """The ONE predicate that gates entry to the RAG resolution store."""
        return (self.status == "validated"
                and self.outcome is not None
                and self.outcome.effective is True
                and self.outcome.validated_by_human)


def make_intervention_id(profile: str, action_id: str, created_at: str) -> str:
    digest = hashlib.sha1(
        f"{profile}|{action_id}|{created_at}".encode("utf-8")).hexdigest()
    return f"INT-{profile[:3].upper()}-{digest[:8]}"


# ── Snapshots (the longitudinal half) ────────────────────────────────────────

class AnalysisSnapshot(BaseModel):
    """The measurable state of one analysis run.

    `metrics` is keyed by finding_key so a later run can look up the same
    finding and compare. A finding that has DISAPPEARED is recorded as absent
    rather than missing — `resolved_keys` makes "the problem is gone" a
    first-class observation instead of a lookup failure."""

    snapshot_id: str
    profile: str
    taken_at: str
    label: str = ""                       # e.g. "tick_04" during replay
    case_count: int = 0
    event_count: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    affected_counts: dict[str, int] = Field(default_factory=dict)
    present_keys: list[str] = Field(default_factory=list)

    def metric_for(self, finding_key: str) -> Optional[float]:
        return self.metrics.get(finding_key)

    def is_resolved(self, finding_key: str) -> bool:
        """The finding was not detected at all in this analysis."""
        return finding_key not in self.present_keys


def make_snapshot_id(profile: str, taken_at: str) -> str:
    digest = hashlib.sha1(f"{profile}|{taken_at}".encode("utf-8")).hexdigest()
    return f"SNAP-{profile[:3].upper()}-{digest[:8]}"
