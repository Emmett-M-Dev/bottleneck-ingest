"""The action-queue export contract — what the dashboard is allowed to rely on.

These tests pin the shape of `ui_actions_<profile>.json`, because the React
"Today" view reads it directly and a silent key rename would break the product
without breaking anything else. They also pin the two things a worker must be
able to trust before clicking approve: that the item says whether approving
touches a file, and that it says what (if anything) was sent to a model.
"""

from __future__ import annotations

import json

import pytest

import config
from actions import lifecycle, store
from actions.execute import create_intervention
from actions.models import (ActionItem, AnalysisSnapshot, BusinessImpact,
                            EvidenceReference)
from actions.outcome import validate
from bridge.export_actions import build_ui_actions


@pytest.fixture
def isolated_outputs(tmp_path, monkeypatch):
    """Point the whole action store at a temp dir — these tests must never
    read or write the real outputs/ state."""
    monkeypatch.setattr(config, "OUTPUTS", tmp_path)
    return tmp_path


def _item(action_id, **kw) -> ActionItem:
    base = dict(
        action_id=action_id, profile="advisory",
        finding_key=f"k::{action_id}", finding_type="stage_sla_breach",
        title=f"title {action_id}", summary="because the data says so",
        stage="Proposal", affected_case_ids=["NA-1", "NA-2"],
        evidence=[EvidenceReference(kind="metric", label="days_over_sla",
                                    metric_label="days_over_sla",
                                    metric_value=5.0)],
        metric_label="days_over_sla", metric_value=5.0,
        action_category="case_action",
        recommended_action="Chase them", action_steps=["Open the list"],
        impact=BusinessImpact(category="revenue_at_risk", revenue_at_risk=1000.0,
                              affected_case_count=2, basis="2 × £500"),
        created_at="2026-07-20", updated_at="2026-07-20",
    )
    base.update(kw)
    return ActionItem(**base)


def _snapshot() -> AnalysisSnapshot:
    return AnalysisSnapshot(snapshot_id="SNAP-X", profile="advisory",
                            taken_at="2026-07-20")


# ── Contract ─────────────────────────────────────────────────────────────────

def test_ui_contract_top_level_keys(isolated_outputs) -> None:
    ui = build_ui_actions("advisory", [_item("A")], snapshot=_snapshot())
    assert set(ui) >= {
        "profile", "workflow", "case_noun", "ui", "currency", "generated_at",
        "analysis_date", "snapshot_id", "totals", "sections", "interventions"}
    assert set(ui["sections"]) == {
        "needs_attention", "revenue_at_risk", "delivery_risks",
        "capacity_constraints", "data_quality"}
    assert set(ui["totals"]) >= {
        "open_items", "revenue_at_risk", "cost_incurred", "hours_at_risk",
        "cases_affected", "by_category", "data_quality_confidence"}


def test_every_item_declares_whether_approving_touches_files(isolated_outputs) -> None:
    items = [
        _item("CASE", action_category="case_action"),
        _item("PROC", action_category="process_intervention"),
        _item("DATA", action_category="data_quality",
              action_template="normalise_status_values"),
        _item("DATA2", action_category="data_quality", action_template=None),
    ]
    ui = build_ui_actions("advisory", items, snapshot=_snapshot())
    flat = [i for section in ui["sections"].values() for i in section]
    by_id = {i["action_id"]: i for i in flat}

    assert by_id["DATA"]["execution"]["will_modify_files"] is True
    for other in ("CASE", "PROC", "DATA2"):
        assert by_id[other]["execution"]["will_modify_files"] is False
        assert by_id[other]["execution"]["route"] == "tracked"
    assert all(i["execution"]["explanation"] for i in flat)


def test_every_item_declares_what_was_sent_to_a_model(isolated_outputs) -> None:
    plain = _item("A")
    with_llm = _item("B", llm_payload={"bottleneck": {"stage": "Proposal"}})
    ui = build_ui_actions("advisory", [plain, with_llm], snapshot=_snapshot())
    flat = {i["action_id"]: i for section in ui["sections"].values()
            for i in section}

    assert flat["A"]["sent_to_llm"]["used_llm"] is False
    assert "Nothing" in flat["A"]["sent_to_llm"]["note"]
    assert flat["B"]["sent_to_llm"]["used_llm"] is True
    assert flat["B"]["sent_to_llm"]["payload"]["bottleneck"]["stage"] == "Proposal"


def test_retrieved_resolutions_survive_the_export(isolated_outputs) -> None:
    """The RAG grounding is the evidence for the recommendation, and once the
    Bottlenecks tab is folded away the exported action item is the only place
    a worker sees it — the export must carry the contents, not just the key."""
    resolutions = [
        {"resolution_id": "RES-001", "similarity_score": 0.82,
         "text": "we added an SLA"},
    ]
    grounded = _item("G", retrieved_resolutions=resolutions)
    ungrounded = _item("U")
    ui = build_ui_actions("advisory", [grounded, ungrounded], snapshot=_snapshot())
    flat = {i["action_id"]: i for section in ui["sections"].values()
            for i in section}

    assert flat["G"]["retrieved_resolutions"] == resolutions
    assert flat["U"]["retrieved_resolutions"] == []


def test_an_item_appears_in_exactly_one_section(isolated_outputs) -> None:
    """A finding with both revenue and delivery risk must not be counted
    twice — a worker reading down the page would act on it twice."""
    both = _item("BOTH", impact=BusinessImpact(
        category="revenue_at_risk", revenue_at_risk=5000.0,
        delivery_risk="high", affected_case_count=2, basis="x"))
    ui = build_ui_actions("advisory", [both], snapshot=_snapshot())
    appearances = [i["action_id"] for section in ui["sections"].values()
                   for i in section]
    assert appearances == ["BOTH"]


def test_closed_items_leave_the_queue(isolated_outputs) -> None:
    items = [_item("OPEN"), _item("DONE", status="validated"),
             _item("NO", status="rejected")]
    ui = build_ui_actions("advisory", items, snapshot=_snapshot())
    open_ids = [i["action_id"] for section in ui["sections"].values()
                for i in section]
    assert open_ids == ["OPEN"]
    assert ui["totals"]["open_items"] == 1


def test_totals_sum_only_the_open_queue(isolated_outputs) -> None:
    items = [
        _item("A", impact=BusinessImpact(revenue_at_risk=1000.0, basis="x")),
        _item("B", impact=BusinessImpact(revenue_at_risk=500.0, basis="x")),
        _item("C", status="dismissed",
              impact=BusinessImpact(revenue_at_risk=99_000.0, basis="x")),
    ]
    ui = build_ui_actions("advisory", items, snapshot=_snapshot())
    assert ui["totals"]["revenue_at_risk"] == 1500.0


def test_intervention_board_separates_active_from_validated(isolated_outputs) -> None:
    item = _item("A")
    active = create_intervention(item, snapshot=_snapshot())
    done = create_intervention(_item("B"), snapshot=_snapshot())
    for status in ("assigned", "in_progress", "completed"):
        lifecycle.transition(done, status)
    store.save_interventions("advisory", [active, done])
    validate("advisory", done.intervention_id, effective=True,
             validated_by="tester")

    ui = build_ui_actions("advisory", [item], snapshot=_snapshot())
    board = ui["interventions"]
    assert [i["intervention_id"] for i in board["active"]] == [active.intervention_id]
    assert [i["intervention_id"] for i in board["recently_validated"]] == [
        done.intervention_id]
    assert board["summary"]["validated_effective"] == 1


def test_export_is_json_serialisable(isolated_outputs) -> None:
    """The whole point of the read model — it has to survive a round trip."""
    ui = build_ui_actions("advisory", [_item("A")], snapshot=_snapshot())
    assert json.loads(json.dumps(ui, ensure_ascii=False))["profile"] == "advisory"


def test_build_refuses_another_smes_event_log(isolated_outputs) -> None:
    """`outputs/event_log.parquet` is one global file every profile overwrites.
    Building a joinery queue while it holds advisory events produced a page
    branded McCrossan Joinery and populated with Northstar Advisory findings —
    stages that do not exist in the joinery workflow, 27 cases where there are
    15. Ingestion stamps the owner; this is the check that uses it."""
    from bridge.export_actions import (WrongProfileError,
                                       _assert_event_log_belongs_to)

    marker = isolated_outputs / "event_log_profile.txt"
    marker.write_text("advisory", encoding="utf-8")

    with pytest.raises(WrongProfileError) as excinfo:
        _assert_event_log_belongs_to("joinery")
    assert "advisory" in str(excinfo.value)
    assert "--profile joinery" in str(excinfo.value)   # tells you how to fix it

    _assert_event_log_belongs_to("advisory")           # the matching case passes


def test_build_allows_an_unstamped_event_log(isolated_outputs) -> None:
    """A log written before the marker existed is not evidence of a mismatch —
    refusing to build on it would break every pre-existing workspace."""
    from bridge.export_actions import _assert_event_log_belongs_to

    _assert_event_log_belongs_to("joinery")


def test_build_allows_a_same_profile_alternate_drive_stamp(
        isolated_outputs, capsys) -> None:
    """`--drive` re-analysis of the SAME profile (e.g. a later snapshot of the
    same SME's drive, or the simulator's world) must not be treated as a
    cross-profile mismatch — CLAUDE.md §4b names this as the mechanism that
    makes a `validated` outcome reachable at all. This must keep working even
    though ingestion now stamps `f"{profile}@{drive}"` for a --drive ingest."""
    from bridge.export_actions import _assert_event_log_belongs_to

    marker = isolated_outputs / "event_log_profile.txt"
    marker.write_text("advisory@data/sim/advisory/drive", encoding="utf-8")

    _assert_event_log_belongs_to("advisory")   # must not raise

    out = capsys.readouterr().out
    assert "data/sim/advisory/drive" in out     # warns, but doesn't block


def test_build_still_refuses_a_different_profiles_alternate_drive_stamp(
        isolated_outputs) -> None:
    """The alternate-drive carve-out must not swallow real cross-profile
    contamination — only the profile segment before '@' is allowed to differ
    in meaning; a genuinely different profile still raises."""
    from bridge.export_actions import (WrongProfileError,
                                       _assert_event_log_belongs_to)

    marker = isolated_outputs / "event_log_profile.txt"
    marker.write_text("advisory@data/sim/advisory/drive", encoding="utf-8")

    with pytest.raises(WrongProfileError):
        _assert_event_log_belongs_to("joinery")


def test_capacity_items_are_not_swallowed_by_delivery_risks(isolated_outputs) -> None:
    """Capacity findings carry a delivery risk too, so section order decides
    where they land. Capacity must get first refusal or the section renders
    empty while every key-person item hides under 'delivery risks'."""
    capacity = _item("CAP", impact=BusinessImpact(
        category="capacity_loss", capacity_pct=90.0, delivery_risk="high",
        affected_case_count=17, basis="one person handles 90%"))
    ui = build_ui_actions("advisory", [capacity], snapshot=_snapshot())
    assert [i["action_id"] for i in ui["sections"]["capacity_constraints"]] == ["CAP"]
    assert ui["sections"]["delivery_risks"] == []


@pytest.mark.parametrize("profile", sorted(config.MESSY_PROFILES))
def test_read_model_builds_for_every_profile(profile: str, isolated_outputs) -> None:
    ui = build_ui_actions(profile, [_item("A", profile=profile)],
                          snapshot=_snapshot())
    assert ui["case_noun"] and ui["workflow"]
    assert ui["ui"]["brand"]
