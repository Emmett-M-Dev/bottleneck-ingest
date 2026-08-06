"""Approval, routing and execution — the corrected Gate-2 behaviour.

The bug this module exists to fix: the previous build ran the status-normalising
remediation executor whenever *any* fix was approved. Approving "chase the four
overdue invoices" would quietly rewrite spreadsheet status columns. Those two
things have nothing to do with each other.

Routing is now decided by the action's category, and there is exactly one path
to automated execution:

    data_quality + a template on MACHINE_EXECUTABLE_TEMPLATES
        -> the remediation executor writes CLEANED COPIES (originals untouched)
    everything else — every case_action, every process_intervention, and every
    data_quality item without a machine-safe template
        -> a tracked Intervention. Nothing is executed. A person does the work.

Approval always creates an Intervention, whichever route it takes, because
every approved action needs a baseline, an owner, a due date and a later
measurement. Execution is a side effect of *some* approvals, never the point
of them.

Process isolation: the executor is launched as its own subprocess (the repo
rule — remediation must never share an interpreter with chromadb/torch), so
this module imports nothing heavy.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from actions import lifecycle, store, templates
from actions.models import (ActionItem, AnalysisSnapshot, Intervention,
                            LifecycleEvent, make_intervention_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContaminatedBaselineError(RuntimeError):
    """Refuse to use a snapshot from a non-default drive — or of unknown
    provenance — as evidence for a real intervention. Guards BOTH doors:

        approve()             stamps `baseline_snapshot_id`/`baseline_value`
                              from a snapshot (the BASELINE side).
        outcome.py::review()  compares that baseline against a LATER
                              snapshot (the OBSERVATION side) to decide
                              whether the fix worked.

    Either side can be contaminated by a simulated demo week; a comparison
    built from either is the only route to `validated`, which promotes a fix
    into trusted RAG knowledge. Left unguarded, a demo run left last would
    let a simulated number become guidance without anyone approving that on
    purpose. See `_snapshot_is_contaminated` / `_assert_snapshot_is_clean`."""


def _resolve_drive(path_like) -> str:
    """Absolute, filesystem-normalised form of a drive path.

    A relative `--drive` argument (e.g. `data/synthetic/messy_advisory`,
    typed from the repo root) must compare EQUAL to the profile's resolved
    default even though the two strings differ — `ingest.py` stamps whatever
    was typed verbatim (`Path(drive).as_posix()`, unresolved, by design —
    see `ingest.py::_write_event_log_owner`), so the burden of normalising
    is on the comparison, not the write. Relative paths resolve against the
    repo root (`config.ROOT`), where every documented invocation runs from —
    not the process's current CWD — so the comparison is stable regardless
    of where a caller happens to be standing."""
    p = Path(path_like)
    if not p.is_absolute():
        p = config.ROOT / p
    return p.resolve().as_posix()


def _default_drive(profile: str) -> str:
    return _resolve_drive(config.MESSY_PROFILES[profile]["dir"])


def _snapshot_is_contaminated(profile: str,
                              snapshot: AnalysisSnapshot | None) -> bool:
    """True when `snapshot` must not be trusted as a baseline or observation
    for a real intervention.

    `snapshot is None` (no snapshot at all) is a different situation, left to
    the caller — it is not itself contamination. Given an actual snapshot:

        source_drive is None   UNKNOWN provenance (every row written before
                               this field existed) — FAILS CLOSED, might be
                               simulated data and there is no way to tell.
        source_drive == ""     the profile's own default drive — clean.
        source_drive == other  an alternate drive — contaminated unless it
                               happens to resolve to the same place as the
                               profile's default (a redundant --drive)."""
    if snapshot is None:
        return False
    if snapshot.source_drive is None:
        return True
    if not snapshot.source_drive:
        return False
    return _resolve_drive(snapshot.source_drive) != _default_drive(profile)


def _assert_snapshot_is_clean(profile: str, snapshot: AnalysisSnapshot | None,
                              *, context: str) -> None:
    """Raise ContaminatedBaselineError if `snapshot` may not be trusted as a
    `context` ("baseline" or "observation") for a real intervention. Shared
    by `approve` (the baseline side, below) and `actions/outcome.py::review`
    (the observation side) so both doors enforce the identical rule."""
    if not _snapshot_is_contaminated(profile, snapshot):
        return
    if snapshot.source_drive is None:
        raise ContaminatedBaselineError(
            f"The {context} snapshot for '{profile}' ({snapshot.snapshot_id}) "
            f"has no recorded provenance — it predates drive-tracking, so it "
            f"could be simulated data and there is no way to tell. Refusing "
            f"to use it as a {context} for a real intervention. Take a fresh "
            f"snapshot and try again:\n"
            f"    python ingest.py --source messy --profile {profile}\n"
            f"    python -m actions.cli queue --profile {profile}")
    raise ContaminatedBaselineError(
        f"The {context} snapshot for '{profile}' was built from "
        f"'{snapshot.source_drive}', not the profile's default drive "
        f"({_default_drive(profile)}). Using it as a {context} would let "
        f"alternate/simulated data influence a real intervention. Re-ingest "
        f"the default drive and rebuild the queue before continuing:\n"
        f"    python ingest.py --source messy --profile {profile}\n"
        f"    python -m actions.cli queue --profile {profile}")


# ── Routing ──────────────────────────────────────────────────────────────────

def route(item: ActionItem) -> str:
    """"machine" or "tracked". The single decision that keeps an approved
    operational action from touching the drive."""
    return "machine" if item.is_machine_executable else "tracked"


def execution_reason(item: ActionItem) -> str:
    """Why this item routed the way it did — surfaced in the UI so the worker
    knows whether approving will change a file or create a task."""
    if item.is_machine_executable:
        return (f"Data-quality fix '{item.action_template}' is on the "
                f"machine-safe list — approving writes cleaned copies and "
                f"leaves the originals untouched.")
    if item.action_category == "data_quality":
        return ("Data-quality work with no machine-safe template — it becomes "
                "a tracked task for a person.")
    if item.action_category == "case_action":
        return ("Case-level work. Approving assigns and tracks it; the system "
                "does not touch any files.")
    return ("Process change. Approving starts a measurable intervention with a "
            "baseline and a review date; the system does not touch any files.")


# ── Approval ─────────────────────────────────────────────────────────────────

def create_intervention(item: ActionItem, *,
                        snapshot: AnalysisSnapshot | None = None,
                        owner: str | None = None, due_date: str | None = None,
                        decision_id: str | None = None,
                        note: str | None = None,
                        actor: str = "human",
                        at: str | None = None) -> Intervention:
    """Turn an approved action item into a tracked commitment.

    The baseline is the metric as it stands RIGHT NOW, captured from the
    snapshot of the analysis the worker was looking at when they approved.
    Without it there is nothing to measure against later, so an intervention
    created without a snapshot is explicitly baseline-less rather than
    silently baselined at zero."""
    stamp = at or _now()
    tpl = templates.template_for(item.profile, item.finding_type)
    fmt = {"stage": item.stage or "this stage", "count": len(item.affected_case_ids),
           "metric": item.metric_value or 0, "subject": "",
           "case_noun": templates.case_noun(item.profile)}

    baseline = None
    baseline_snapshot_id = None
    if snapshot is not None:
        baseline = snapshot.metric_for(item.finding_key)
        baseline_snapshot_id = snapshot.snapshot_id
    if baseline is None:
        baseline = item.metric_value

    review_days = templates.due_days(item.profile, item.action_category) * 2
    review_from = (due_date or item.due_date or stamp[:10])
    try:
        review_date = (datetime.fromisoformat(review_from).date()
                       + timedelta(days=review_days)).isoformat()
    except ValueError:
        review_date = None

    intervention = Intervention(
        intervention_id=make_intervention_id(item.profile, item.action_id, stamp),
        action_id=item.action_id,
        profile=item.profile,
        finding_key=item.finding_key,
        action_category=item.action_category,
        title=item.title,
        recommended_action=item.recommended_action,
        action_steps=list(item.action_steps),
        status="proposed",
        owner=owner or item.owner,
        due_date=due_date or item.due_date,
        review_date=review_date,
        success_metric=templates.render(tpl.get("success_metric", ""), **fmt),
        metric_label=item.metric_label,
        baseline_value=baseline,
        baseline_snapshot_id=baseline_snapshot_id,
        expected_improvement_pct=tpl.get("expected_improvement_pct"),
        improvement_is_decrease=True,
        created_at=stamp,
        updated_at=stamp,
        decision_id=decision_id,
        case_snapshot={"type": item.finding_type, "stage": item.stage,
                       "title": item.title, "description": item.summary,
                       "affected_case_ids": item.affected_case_ids[:20]},
        history=[LifecycleEvent(at=stamp, from_status=None, to_status="proposed",
                                actor=actor, note="raised from the action queue")],
    )
    lifecycle.transition(intervention, "approved", actor=actor,
                         note=note or "approved at Gate 2", at=stamp)
    return intervention


# ── Machine execution (the narrow, safe path) ────────────────────────────────

def run_remediation(profile: str, *, apply: bool = True,
                    runner=subprocess.run) -> dict:
    """Shell the remediation executor in its own process.

    `runner` is injectable so tests exercise the routing without spawning a
    Python interpreter or writing files."""
    cmd = [sys.executable, "-m", "remediate.run", "--profile", profile]
    if apply:
        cmd.append("--apply")
    # Decode as UTF-8 explicitly. The executor echoes the status values it is
    # normalising, and those contain ✔ — which the Windows ANSI codepage that
    # `text=True` would otherwise pick cannot decode.
    proc = runner(cmd, capture_output=True, cwd=str(config.ROOT),
                  encoding="utf-8", errors="replace",
                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    def tail(text: str) -> str:
        return "\n".join((text or "").strip().splitlines()[-5:])

    return {"command": " ".join(cmd[1:]),
            "returncode": getattr(proc, "returncode", 1),
            "stdout_tail": tail(getattr(proc, "stdout", "")),
            "stderr_tail": tail(getattr(proc, "stderr", ""))}


def execute(item: ActionItem, *, runner=subprocess.run) -> dict:
    """Carry out whatever this approval actually authorises.

    Returns a record of what happened — including, for the overwhelmingly
    common tracked case, an explicit statement that nothing was executed. The
    UI shows that sentence; silence about automation is how a system ends up
    surprising people."""
    if not item.is_machine_executable:
        return {"executed": False, "mode": "tracked",
                "reason": execution_reason(item),
                "category": item.action_category}
    result = run_remediation(item.profile, apply=True, runner=runner)
    return {"executed": result["returncode"] == 0, "mode": "machine",
            "reason": execution_reason(item),
            "category": item.action_category,
            "template": item.action_template, "result": result}


def approve(profile: str, item: ActionItem, *,
            snapshot: AnalysisSnapshot | None = None,
            owner: str | None = None, due_date: str | None = None,
            decision_id: str | None = None, note: str | None = None,
            actor: str = "human", runner=subprocess.run,
            path=None, execute_machine: bool = True) -> tuple[Intervention, dict]:
    """The whole Gate-2 approval: create the intervention, persist it, and run
    only what is genuinely machine-safe."""
    _assert_snapshot_is_clean(profile, snapshot, context="baseline")
    intervention = create_intervention(
        item, snapshot=snapshot, owner=owner, due_date=due_date,
        decision_id=decision_id, note=note, actor=actor)
    if intervention.owner:
        lifecycle.transition(intervention, "assigned", actor=actor,
                             note=f"owner: {intervention.owner}")
    store.upsert_intervention(profile, intervention, path)

    item.status = intervention.status
    item.owner = intervention.owner
    item.due_date = intervention.due_date
    item.intervention_id = intervention.intervention_id
    item.updated_at = intervention.updated_at

    if execute_machine:
        outcome = execute(item, runner=runner)
    else:
        outcome = {"executed": False, "mode": "tracked",
                   "reason": execution_reason(item),
                   "category": item.action_category}
    return intervention, outcome


def reject(profile: str, item: ActionItem, *, reason: str | None = None,
           actor: str = "human", dismissed: bool = False,
           path=None) -> Intervention:
    """A rejection is still recorded. The audit trail must show what was
    proposed and turned down, and it must never become guidance."""
    stamp = _now()
    intervention = create_intervention(item, decision_id=None, actor=actor,
                                       note="raised for a decision", at=stamp)
    # create_intervention leaves it at "approved"; walk it back to the real
    # verdict so the history reads honestly.
    intervention.status = "proposed"
    intervention.history = intervention.history[:1]
    lifecycle.transition(intervention, "dismissed" if dismissed else "rejected",
                         actor=actor, note=reason, at=stamp)
    store.upsert_intervention(profile, intervention, path)
    item.status = intervention.status
    item.intervention_id = intervention.intervention_id
    item.updated_at = stamp
    return intervention
