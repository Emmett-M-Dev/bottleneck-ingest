"""What to DO about each kind of finding — and, critically, which of the three
kinds of recommendation it is.

The category is the routing decision that keeps the system honest:

    data_quality          the data is wrong. Some of these are machine-safe and
                          run through the remediation executor's cleaned-copy
                          flow after approval. The rest are human data work.
    case_action           a person must do something to specific cases. NEVER
                          machine-executed — approving "chase the overdue
                          invoice" must not launch a spreadsheet cleaner.
    process_intervention  a change to how the work runs. Becomes a measurable
                          experiment with a baseline, an owner and a review date.

Templates are plain format strings so a profile can override the wording
(config `MESSY_PROFILES[<p>]["actions"]["templates"]`) without touching code.
`{stage}`, `{count}`, `{metric}`, `{subject}` and `{case_noun}` interpolate.
"""

from __future__ import annotations

import config
from actions.models import ACTION_CATEGORIES

# The machine-safe data-quality template. Its id must match an entry in
# actions.models.MACHINE_EXECUTABLE_TEMPLATES for execution to be permitted.
NORMALISE_STATUS = "normalise_status_values"

DEFAULT_TEMPLATES: dict[str, dict] = {
    # ── Process-level: how the work is run ──────────────────────────────────
    "delay": {
        "category": "process_intervention",
        "action": "Set and police an entry SLA for {stage}",
        "steps": [
            "Agree the number of days work should take to reach {stage}.",
            "Flag any {case_noun} that passes that limit on the tracker.",
            "Review the flagged {case_noun}s once a week and clear the blocker behind each.",
        ],
        "success_metric": "average days to reach {stage} falls",
        "expected_improvement_pct": 40.0,
    },
    "repetition": {
        "category": "process_intervention",
        "action": "Make the first pass at {stage} the only one",
        "steps": [
            "Write a short checklist for {stage} so the first pass captures everything.",
            "Record the outcome on the {case_noun} row itself where everyone can see it.",
            "Only repeat {stage} when that recorded outcome is genuinely missing.",
        ],
        "success_metric": "{case_noun}s repeating {stage} falls",
        "expected_improvement_pct": 50.0,
    },
    "rework": {
        "category": "process_intervention",
        "action": "Confirm before leaving {stage}, and hold a reserve",
        "steps": [
            "Re-confirm the inputs to {stage} at completion, not only at the start.",
            "Keep a small reserve of people, slots or stock to absorb fall-throughs.",
            "Record the reason for every loop-back so repeat causes surface early.",
        ],
        "success_metric": "{case_noun}s looping back to {stage} falls",
        "expected_improvement_pct": 35.0,
    },
    "overloaded_owner": {
        "category": "process_intervention",
        "action": "Rebalance {subject}'s workload",
        "steps": [
            "Review the {count} open {case_noun}s {subject} is holding.",
            "Move the ones that do not need their specific knowledge to a colleague.",
            "Agree a working limit and check it at the weekly review.",
        ],
        "success_metric": "open {case_noun}s held by any one person falls below the limit",
        "expected_improvement_pct": 40.0,
    },
    "key_person_dependency": {
        "category": "process_intervention",
        "action": "Cross-train a second person on {stage}",
        "steps": [
            "Pick a colleague to shadow {subject} through {stage}.",
            "Write down the parts of {stage} that only {subject} currently knows.",
            "Route the next few {case_noun}s at {stage} to the second person.",
        ],
        "success_metric": "one person's share of {stage} work falls below 60%",
        "expected_improvement_pct": 30.0,
    },

    # ── Case-level: specific cases needing a human ──────────────────────────
    "stage_sla_breach": {
        "category": "case_action",
        "action": "Chase the {count} {case_noun}(s) sitting past the {stage} limit",
        "steps": [
            "Open each listed {case_noun}, oldest first.",
            "Make the next contact or hand it to whoever can move it.",
            "Record what happened on the {case_noun} row so the clock resets honestly.",
        ],
        "success_metric": "no {case_noun} sits past the {stage} limit",
        "expected_improvement_pct": 80.0,
    },
    "stalled_case": {
        "category": "case_action",
        "action": "Revive or close the {count} {case_noun}(s) that have gone quiet",
        "steps": [
            "Check each listed {case_noun} for what it is actually waiting on.",
            "Restart it, or close it honestly if it is dead.",
            "Note the reason — repeated reasons are a process problem, not a case one.",
        ],
        "success_metric": "idle {case_noun}s fall to zero",
        "expected_improvement_pct": 80.0,
    },
    "unowned_case": {
        "category": "case_action",
        "action": "Assign an owner to {count} unowned {case_noun}(s)",
        "steps": [
            "Put a name against each listed {case_noun}.",
            "Tell that person it is theirs.",
            "Make an owner mandatory before a {case_noun} leaves its first stage.",
        ],
        "success_metric": "unowned open {case_noun}s reach zero",
        "expected_improvement_pct": 100.0,
    },
    "unrealised_value": {
        "category": "case_action",
        "action": "Bill or chase the {count} {case_noun}(s) holding unrealised value",
        "steps": [
            "Work the list highest value first.",
            "Raise the invoice, or chase the approval that is blocking it.",
            "Record the invoice reference against the {case_noun}.",
        ],
        "success_metric": "value sitting before a revenue stage falls",
        "expected_improvement_pct": 70.0,
    },
    "anomaly": {
        "category": "case_action",
        "action": "Check whether this observation is real before acting on it",
        "steps": [
            "Look at the stage the model flagged and decide if the pattern is genuine.",
            "If it is real, raise it as a proper finding; if not, dismiss it.",
        ],
        "success_metric": "the observation is confirmed or dismissed",
        "expected_improvement_pct": None,
    },

    # ── Data quality ────────────────────────────────────────────────────────
    "messy_status_values": {
        "category": "data_quality",
        "template_id": NORMALISE_STATUS,
        "action": "Normalise {count} inconsistent status value(s) to a fixed set",
        "steps": [
            "Review the proposed before → after value map.",
            "Approve it to write cleaned copies — the original files are never touched.",
            "Use the fixed set from now on so the mess does not return.",
        ],
        "success_metric": "inconsistent status cells fall to zero",
        "expected_improvement_pct": 100.0,
    },
    "stale_duplicate_file": {
        "category": "data_quality",
        "action": "Decide which copy of {stage} is the real one",
        "steps": [
            "Compare the overlapping files and pick the authoritative copy.",
            "Rename or archive the others so nobody edits the wrong one.",
            "Re-run the mapping review so the pipeline reads only the survivor.",
        ],
        "success_metric": "one authoritative copy per source",
        "expected_improvement_pct": None,
    },
}


def _profile_actions(profile: str) -> dict:
    return (config.MESSY_PROFILES.get(profile, {}).get("actions") or {})


def case_noun(profile: str) -> str:
    return _profile_actions(profile).get("case_noun", "case")


def workflow_name(profile: str) -> str:
    cfg = config.MESSY_PROFILES.get(profile, {})
    return (cfg.get("actions") or {}).get(
        "workflow", (cfg.get("ui") or {}).get("context", "Operations"))


def template_for(profile: str, finding_type: str) -> dict:
    """The template for a finding type, with any per-profile override folded in.
    Unknown types fall back to a neutral case-action so the queue never drops a
    finding just because a new detector was added."""
    overrides = (_profile_actions(profile).get("templates") or {})
    base = DEFAULT_TEMPLATES.get(finding_type, {
        "category": "case_action",
        "action": "Review the {stage} finding and decide what to do",
        "steps": ["Look at the affected {case_noun}s and agree an action."],
        "success_metric": "the finding no longer appears",
        "expected_improvement_pct": None,
    })
    merged = {**base, **(overrides.get(finding_type) or {})}
    if merged.get("category") not in ACTION_CATEGORIES:
        merged["category"] = "case_action"
    return merged


def render(text: str, **fmt) -> str:
    """Interpolate a template safely — a profile override with a stray brace
    must not crash an analysis run."""
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return text


def due_days(profile: str, category: str) -> int:
    """How long a category of work gets before it is overdue. Config-driven so
    a business with a slower rhythm can say so."""
    defaults = {"data_quality": 7, "case_action": 3, "process_intervention": 21}
    configured = _profile_actions(profile).get("due_days") or {}
    return int(configured.get(category, defaults[category]))


def default_owner(profile: str, category: str) -> str | None:
    """A suggested owner, not an assignment. The worker confirms or changes it
    at the gate — the system never silently allocates someone's time."""
    actions = _profile_actions(profile)
    by_category = actions.get("owner_by_category") or {}
    return by_category.get(category, actions.get("default_owner"))
