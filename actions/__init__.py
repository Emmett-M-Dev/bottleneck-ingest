"""The operational action layer — what turns a detected finding into work.

The pipeline core answers "what is wrong with this workflow". This package
answers the questions a worker actually has: what needs attention today, which
cases, why, what it costs, what to do, who owns it, by when, and — after a
later analysis — whether it worked.

Nothing in here is SME-specific. Labels, stage ordering, monetary assumptions
and the available action templates come from `config.MESSY_PROFILES[<p>]`;
the models, lifecycle and ranking are identical for every profile.

IMPORT CONSTRAINT: this package must stay free of chromadb / pyarrow / torch
so it can run inside the remediation/audit-style light processes. pydantic and
(in build.py) pandas are the heaviest things allowed.
"""

from actions.models import (ActionCategory, ActionItem, ActionStatus,
                            AnalysisSnapshot, BusinessImpact, EvidenceReference,
                            Intervention, InterventionOutcome, LifecycleEvent)

__all__ = [
    "ActionCategory", "ActionItem", "ActionStatus", "AnalysisSnapshot",
    "BusinessImpact", "EvidenceReference", "Intervention",
    "InterventionOutcome", "LifecycleEvent",
]
