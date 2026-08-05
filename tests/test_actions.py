"""Action-layer tests: the models, the store's merge semantics, deterministic
ranking, the impact arithmetic, and — the two behavioural corrections this
layer exists for — execution routing by category and the outcome-gated
lifecycle.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import config
from actions import lifecycle, store
from actions.build import (build_action_items, build_snapshot,
                           data_quality_confidence)
from actions.execute import (ContaminatedBaselineError, approve,
                             create_intervention, execute, route)
from actions.impact import build_impact
from actions.models import (ActionItem, AnalysisSnapshot, BusinessImpact,
                            EvidenceReference, make_action_id)
from actions.outcome import compare, review, validate
from actions.rank import rank

PROFILES = sorted(config.MESSY_PROFILES)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _item(action_id="ACT-TST-0001", **kw) -> ActionItem:
    base = dict(
        action_id=action_id, profile="foyle",
        finding_key="delay::booking confirmed::avg_delay_days",
        finding_type="delay", title="Cases stall entering Booking Confirmed",
        affected_case_ids=["B-001", "B-002"], metric_label="avg_delay_days",
        metric_value=14.0, action_category="process_intervention",
        created_at="2026-07-01", updated_at="2026-07-01",
    )
    base.update(kw)
    return ActionItem(**base)


def _snapshot(metrics: dict, *, snapshot_id="SNAP-A", present=None,
              taken_at="2026-07-01") -> AnalysisSnapshot:
    return AnalysisSnapshot(
        snapshot_id=snapshot_id, profile="foyle", taken_at=taken_at,
        metrics=metrics,
        present_keys=list(metrics) if present is None else present)


class _FakeProc:
    returncode = 0
    stdout = "applied"
    stderr = ""


class _Runner:
    """Stands in for subprocess.run so routing is testable without spawning
    an interpreter or writing any files."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return _FakeProc()


# ── Evidence + impact ────────────────────────────────────────────────────────

def test_evidence_reference_splits_a_source_ref() -> None:
    ref = EvidenceReference.from_source_ref(
        "bookings Jan-May 2026.xlsx:Sheet1:14", label="B-001", case_id="B-001")
    assert ref.source_file == "bookings Jan-May 2026.xlsx"
    assert ref.sheet == "Sheet1"
    assert ref.row == 14
    assert ref.kind == "source_row"


def test_impact_basis_is_always_populated_and_explains_the_sum() -> None:
    impact = build_impact("foyle", "delay", affected=5, metric=12.0)
    assert impact.cost_incurred == 5 * 12 * config.MESSY_PROFILES["foyle"]["costs"]["delay_day_cost"]
    assert "5 case(s) × 12 days" in impact.basis
    assert impact.is_projection is True


def test_recorded_values_are_observations_not_projections() -> None:
    details = [{"case_id": "P-1", "value": 4000.0}, {"case_id": "P-2", "value": 2000.0}]
    impact = build_impact("foyle", "unrealised_value", affected=2,
                          metric=6000.0, case_details=details)
    assert impact.revenue_at_risk == 6000.0
    assert impact.is_projection is False       # summed from the SME's own sheets
    assert impact.category == "revenue_at_risk"


def test_missing_values_fall_back_to_the_average_and_stay_projected() -> None:
    details = [{"case_id": "P-1", "value": None}, {"case_id": "P-2", "value": None}]
    impact = build_impact("foyle", "stalled_case", affected=2, metric=30.0,
                          case_details=details)
    assert impact.is_projection is True


# ── Ranking ──────────────────────────────────────────────────────────────────

def test_ranking_is_deterministic_and_explainable() -> None:
    small = _item("ACT-A", impact=BusinessImpact(revenue_at_risk=500.0,
                                                 affected_case_count=1),
                  affected_case_ids=["B-1"])
    big = _item("ACT-B", impact=BusinessImpact(revenue_at_risk=50_000.0,
                                               delivery_risk="high",
                                               affected_case_count=9),
                affected_case_ids=[f"B-{i}" for i in range(9)])
    today = date(2026, 7, 1)

    first_pass = [i.action_id for i in rank([small, big], today=today)]
    second_pass = [i.action_id for i in rank([big, small], today=today)]
    assert first_pass == second_pass == ["ACT-B", "ACT-A"]
    assert big.rank_score > small.rank_score
    assert any("at risk" in line for line in big.rank_explanation)
    assert big.rank_explanation, "every score must carry its reasons"


def test_low_confidence_findings_are_scaled_down() -> None:
    certain = _item("ACT-A", detection_confidence=1.0,
                    impact=BusinessImpact(revenue_at_risk=1000.0))
    shaky = _item("ACT-B", detection_confidence=0.2,
                  impact=BusinessImpact(revenue_at_risk=1000.0))
    rank([certain, shaky], today=date(2026, 7, 1))
    assert certain.rank_score > shaky.rank_score


def test_overdue_outranks_a_distant_due_date() -> None:
    overdue = _item("ACT-A", due_date="2026-06-01")
    later = _item("ACT-B", due_date="2026-09-01")
    ordered = rank([later, overdue], today=date(2026, 7, 1))
    assert ordered[0].action_id == "ACT-A"


# ── Store ────────────────────────────────────────────────────────────────────

def test_merge_keeps_human_edits_and_refreshes_the_evidence(tmp_path) -> None:
    existing = _item(owner="Ciara", due_date="2026-08-01", status="in_progress",
                     metric_value=14.0)
    fresh = _item(metric_value=6.0, owner=None, status="proposed")

    merged = store.merge_actions([existing], [fresh])
    assert len(merged) == 1
    assert merged[0].owner == "Ciara"          # the worker's assignment survives
    assert merged[0].status == "in_progress"
    assert merged[0].metric_value == 6.0       # the measurement is refreshed


def test_merge_keeps_items_whose_finding_stopped_recurring() -> None:
    standing = _item("ACT-OLD", finding_key="rework::x::loop_back_cases",
                     status="assigned")
    merged = store.merge_actions([standing], [_item("ACT-NEW")])
    assert {i.action_id for i in merged} == {"ACT-OLD", "ACT-NEW"}


def test_action_ids_are_stable_across_runs() -> None:
    key = "delay::proposal::avg_delay_days"
    assert make_action_id("advisory", key) == make_action_id("advisory", key)
    assert make_action_id("advisory", key) != make_action_id("foyle", key)


# ── Execution routing (behavioural correction #1) ────────────────────────────

def test_operational_approval_never_invokes_remediation() -> None:
    runner = _Runner()
    for category in ("case_action", "process_intervention"):
        item = _item(action_category=category)
        assert route(item) == "tracked"
        result = execute(item, runner=runner)
        assert result["executed"] is False
        assert result["mode"] == "tracked"
    assert runner.calls == [], "no subprocess may be launched for operational work"


def test_data_quality_without_a_safe_template_is_also_tracked() -> None:
    runner = _Runner()
    item = _item(action_category="data_quality", action_template=None)
    assert execute(item, runner=runner)["executed"] is False
    assert runner.calls == []


def test_machine_safe_data_fix_invokes_the_cleaned_copy_flow() -> None:
    runner = _Runner()
    item = _item(action_category="data_quality",
                 action_template="normalise_status_values")
    assert route(item) == "machine"
    result = execute(item, runner=runner)
    assert result["executed"] is True
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert "remediate.run" in cmd and "--apply" in cmd


def test_approval_of_a_case_action_still_creates_a_tracked_intervention(tmp_path) -> None:
    runner = _Runner()
    path = tmp_path / "interventions.json"
    item = _item(action_category="case_action", owner="Ciara")
    snapshot = _snapshot({item.finding_key: 14.0})

    intervention, outcome = approve("foyle", item, snapshot=snapshot,
                                    runner=runner, path=path)
    assert runner.calls == []
    assert outcome["mode"] == "tracked"
    assert intervention.baseline_value == 14.0
    assert intervention.status == "assigned"   # an owner was supplied
    assert item.intervention_id == intervention.intervention_id
    assert store.load_interventions("foyle", path)[0].baseline_value == 14.0


# ── Baseline-drive guard (a simulated snapshot must never baseline a real
#    intervention — see actions/execute.py::_snapshot_is_contaminated) ───────

def test_approve_refuses_a_baseline_from_a_non_default_drive(tmp_path) -> None:
    runner = _Runner()
    path = tmp_path / "interventions.json"
    item = _item(action_category="case_action", owner="Ciara")
    snapshot = _snapshot({item.finding_key: 14.0})
    snapshot.source_drive = "data/sim/foyle/drive"  # not foyle's default dir

    with pytest.raises(ContaminatedBaselineError):
        approve("foyle", item, snapshot=snapshot, runner=runner, path=path)

    assert store.load_interventions("foyle", path) == [], (
        "a refused approval must not persist an intervention")


def test_approve_accepts_a_baseline_from_the_default_drive(tmp_path) -> None:
    """The normal path: `source_drive` empty (a plain ingest) is never
    contaminated, and approval proceeds exactly as before."""
    runner = _Runner()
    path = tmp_path / "interventions.json"
    item = _item(action_category="case_action", owner="Ciara")
    snapshot = _snapshot({item.finding_key: 14.0})
    assert snapshot.source_drive == ""

    intervention, outcome = approve("foyle", item, snapshot=snapshot,
                                    runner=runner, path=path)
    assert intervention.baseline_value == 14.0
    assert intervention.baseline_snapshot_id == snapshot.snapshot_id
    assert outcome["mode"] == "tracked"


def test_approve_accepts_a_baseline_explicitly_from_the_profiles_own_default(
        tmp_path) -> None:
    """A snapshot whose `source_drive` happens to equal the profile's own
    default folder (an explicit but redundant --drive) is not contaminated
    either — only a genuinely alternate drive is refused."""
    runner = _Runner()
    path = tmp_path / "interventions.json"
    item = _item(action_category="case_action", owner="Ciara")
    snapshot = _snapshot({item.finding_key: 14.0})
    snapshot.source_drive = config.MESSY_PROFILES["foyle"]["dir"].as_posix()

    intervention, _ = approve("foyle", item, snapshot=snapshot,
                              runner=runner, path=path)
    assert intervention.baseline_value == 14.0


# ── Lifecycle + outcome gating (behavioural correction #2) ───────────────────

def test_illegal_transitions_are_refused() -> None:
    item = _item()
    intervention = create_intervention(item)
    with pytest.raises(lifecycle.TransitionError):
        lifecycle.transition(intervention, "validated")


def test_lifecycle_history_records_every_move() -> None:
    intervention = create_intervention(_item())
    lifecycle.transition(intervention, "assigned", note="to Ciara")
    lifecycle.transition(intervention, "in_progress")
    lifecycle.transition(intervention, "completed")
    assert [e.to_status for e in intervention.history] == [
        "proposed", "approved", "assigned", "in_progress", "completed"]


def test_approved_but_unmeasured_is_not_trusted_knowledge() -> None:
    intervention = create_intervention(_item())
    assert intervention.status == "approved"
    assert intervention.is_trusted_knowledge is False


def test_compare_calls_a_resolved_finding_effective() -> None:
    item = _item()
    intervention = create_intervention(item, snapshot=_snapshot({item.finding_key: 14.0}))
    later = _snapshot({}, snapshot_id="SNAP-B", present=[])
    outcome = compare(intervention, later)
    assert outcome.effective is True
    assert outcome.observed_value == 0.0
    assert "no longer detected" in outcome.effective_reason


def test_compare_refuses_to_judge_a_change_inside_the_noise_band() -> None:
    item = _item()
    intervention = create_intervention(item, snapshot=_snapshot({item.finding_key: 14.0}))
    later = _snapshot({item.finding_key: 13.5}, snapshot_id="SNAP-B")
    outcome = compare(intervention, later)
    assert outcome.effective is None            # not enough movement to call
    assert "noise band" in outcome.effective_reason


def test_compare_marks_a_worsening_metric_ineffective() -> None:
    item = _item()
    intervention = create_intervention(item, snapshot=_snapshot({item.finding_key: 10.0}))
    later = _snapshot({item.finding_key: 20.0}, snapshot_id="SNAP-B")
    outcome = compare(intervention, later)
    assert outcome.effective is False
    assert outcome.direction == "worsened"


def test_expected_value_is_a_projection_and_never_decides_the_verdict() -> None:
    item = _item()
    intervention = create_intervention(item, snapshot=_snapshot({item.finding_key: 10.0}))
    later = _snapshot({item.finding_key: 10.0}, snapshot_id="SNAP-B")
    outcome = compare(intervention, later)
    assert outcome.expected_value is not None   # the promise was recorded
    assert outcome.observed_value == 10.0       # reality did not move
    assert outcome.effective is None


def test_review_only_measures_completed_work(tmp_path) -> None:
    path = tmp_path / "interventions.json"
    item = _item()
    baseline = _snapshot({item.finding_key: 14.0})
    untouched = create_intervention(item, snapshot=baseline)
    done = create_intervention(_item("ACT-DONE"), snapshot=baseline)
    for status in ("assigned", "in_progress", "completed"):
        lifecycle.transition(done, status)
    store.save_interventions("foyle", [untouched, done], path)

    later = _snapshot({}, snapshot_id="SNAP-B", present=[])
    changed = review("foyle", later, path=path)
    assert [i.intervention_id for i in changed] == [done.intervention_id]

    stored = {i.intervention_id: i for i in store.load_interventions("foyle", path)}
    assert stored[done.intervention_id].status == "outcome_review"
    assert stored[untouched.intervention_id].status == "approved"


def test_validation_is_required_before_knowledge_is_trusted(tmp_path) -> None:
    path = tmp_path / "interventions.json"
    item = _item()
    intervention = create_intervention(item, snapshot=_snapshot({item.finding_key: 14.0}))
    for status in ("assigned", "in_progress", "completed"):
        lifecycle.transition(intervention, status)
    store.save_interventions("foyle", [intervention], path)

    review("foyle", _snapshot({}, snapshot_id="SNAP-B", present=[]), path=path)
    measured = store.load_interventions("foyle", path)[0]
    assert measured.outcome.effective is True
    assert measured.is_trusted_knowledge is False   # measured, not yet confirmed

    validated = validate("foyle", intervention.intervention_id,
                         effective=True, validated_by="Emmett", path=path)
    assert validated.status == "validated"
    assert validated.is_trusted_knowledge is True


def test_human_can_overrule_the_automated_reading(tmp_path) -> None:
    path = tmp_path / "interventions.json"
    item = _item()
    intervention = create_intervention(item, snapshot=_snapshot({item.finding_key: 14.0}))
    for status in ("assigned", "in_progress", "completed"):
        lifecycle.transition(intervention, status)
    store.save_interventions("foyle", [intervention], path)
    review("foyle", _snapshot({}, snapshot_id="SNAP-B", present=[]), path=path)

    result = validate("foyle", intervention.intervention_id, effective=False,
                      validated_by="Emmett", note="the drop was seasonal", path=path)
    assert result.status == "ineffective"
    assert result.is_trusted_knowledge is False
    assert "overruled" in result.outcome.effective_reason


# ── Snapshots + queue construction ───────────────────────────────────────────

def _tiny_log() -> pd.DataFrame:
    rows = [
        ("B-1", "Request Received", "2026-01-01", "[PERSON_1]", "done", "f.xlsx:S:2"),
        ("B-1", "Placement Offer", "2026-01-05", "[PERSON_1]", "done", "f.xlsx:S:3"),
        ("B-2", "Request Received", "2026-01-02", None, "", "f.xlsx:S:4"),
        ("B-2", "Placement Offer", "2026-02-20", None, "", "f.xlsx:S:5"),
    ]
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp",
                                     "actor", "status", "source_ref"])
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"])
    df["value"] = [1000.0, 1000.0, None, None]
    return df


def test_data_quality_confidence_reports_its_reasons() -> None:
    score, why = data_quality_confidence(_tiny_log())
    assert 0.0 <= score <= 1.0
    assert "no owner" in why


def test_snapshot_keys_line_up_with_the_items_that_produced_them() -> None:
    df = _tiny_log()
    items = build_action_items("foyle", df)
    snapshot = build_snapshot("foyle", df, items, label="test")
    for item in items:
        assert item.finding_key in snapshot.present_keys
        assert snapshot.is_resolved(item.finding_key) is False
    assert snapshot.is_resolved("delay::nowhere::avg_delay_days") is True


def test_every_item_carries_affected_cases_or_explicit_evidence() -> None:
    df = _tiny_log()
    for item in build_action_items("foyle", df):
        assert item.recommended_action, item.action_id
        assert item.action_category in (
            "data_quality", "case_action", "process_intervention")
        assert item.evidence or item.affected_case_ids, item.action_id


@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_can_build_a_queue_from_the_same_code(profile: str) -> None:
    """The generalisability claim, in the action layer: one code path, every
    SME, no per-profile branches."""
    df = _tiny_log()
    items = build_action_items(profile, df)
    ranked = rank(items, today=date(2026, 3, 1))
    assert all(i.profile == profile for i in ranked)
    assert all(i.workflow for i in ranked)


def test_structural_items_carry_the_retrieved_resolutions():
    """The RAG grounding must reach the action item — it is the evidence for
    the recommendation, and Today is the only place a worker sees it."""
    import pandas as pd
    from actions.build import build_action_items

    rows = []
    for n in range(1, 4):
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", pd.Timestamp("2026-01-01"), "R", "done", "x.xlsx:1", 1000),
            (cid, "Qualification", pd.Timestamp("2026-01-03"), "R", "done", "x.xlsx:2", 1000),
            (cid, "Proposal", pd.Timestamp("2026-01-28"), "R", "done", "x.xlsx:3", 1000),
        ]
    for n in range(4, 9):                      # background so the gap is an outlier
        cid = f"NA-{n}"
        rows += [
            (cid, "Lead", pd.Timestamp("2026-01-01"), "R", "done", "x.xlsx:4", 1000),
            (cid, "Qualification", pd.Timestamp("2026-01-03"), "R", "done", "x.xlsx:5", 1000),
            (cid, "Proposal", pd.Timestamp("2026-01-05"), "R", "done", "x.xlsx:6", 1000),
        ]
    df = pd.DataFrame(rows, columns=["case_id", "stage", "ts", "actor",
                                     "status", "source_ref", "value"])

    cases = [{
        "case_id": "BN001",
        "finding_key": "delay::proposal::avg_delay_days",
        "type": "delay",
        "title": "Proposals waiting",
        "description": "they sit",
        "retrieved_resolutions": [
            {"resolution_id": "RES-001", "similarity_score": 0.82,
             "text": "we added an SLA"},
        ],
    }]

    items = build_action_items("advisory", df, cases=cases)
    delays = [i for i in items if i.finding_type == "delay"]
    assert delays, "expected a delay finding"
    assert delays[0].retrieved_resolutions[0]["resolution_id"] == "RES-001"


def test_items_without_a_diagnosis_have_no_resolutions():
    """Absent RAG grounding the field is empty, never None — the UI maps over it."""
    import pandas as pd
    from actions.models import ActionItem

    item = ActionItem(action_id="A", profile="advisory", finding_key="k",
                      finding_type="delay", title="t")
    assert item.retrieved_resolutions == []
