"""Case-level detection tests, plus the Northstar Advisory profile.

Two things are being pinned here. First, that the generic rules behave the way
their names claim on small hand-built logs — no SME vocabulary anywhere in the
rule code. Second, that the professional-services drive really does contain the
operational patterns it advertises, and that the SAME rule code finds them for
every profile.

The circularity guard applies as it does elsewhere: the generator records only
where it parked each engagement, and the rules decide independently whether
that breaches anything.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

import config
from detection.case_rules import DEFAULT_RULES, detect_case_findings, rules_for

PROFILES = sorted(config.MESSY_PROFILES)


def _log(rows: list[tuple]) -> pd.DataFrame:
    """rows = (case_id, stage, date, actor, value)"""
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp",
                                     "actor", "value"])
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"])
    df["status"] = ""
    df["source_ref"] = [f"sheet.xlsx:S:{i + 2}" for i in range(len(df))]
    return df


_CFG = {
    "stage_order": ["Lead", "Proposal", "Won", "Paid"],
    "costs": {"currency": "£"},
    "case_rules": {
        "stage_sla_days": {"Lead": 3, "Proposal": 7},
        "default_stalled_days": 30,
        "terminal_stages": ["Paid"],
        "revenue_stages": ["Paid"],
        "owner_load_limit": 2,
        "key_person_min_share": 0.6,
        "key_person_min_events": 4,
        "min_affected": 1,
    },
}


def _by_type(findings) -> dict:
    return {f.type: f for f in findings}


# ── Individual rules ─────────────────────────────────────────────────────────

def test_rules_fall_back_to_defaults_for_a_profile_with_no_block() -> None:
    assert rules_for({}) == DEFAULT_RULES
    assert rules_for({"case_rules": {"owner_load_limit": 9}})["owner_load_limit"] == 9


def test_stage_sla_breach_flags_only_cases_past_their_limit() -> None:
    df = _log([
        ("A", "Lead", "2026-01-01", "Ruairi", None),      # 9 days stale, limit 3
        ("B", "Lead", "2026-01-08", "Ruairi", None),      # 2 days, within limit
        ("C", "Lead", "2026-01-10", "Meabh", None),       # today
    ])
    finding = _by_type(detect_case_findings(df, _CFG))["stage_sla_breach"]
    assert finding.affected_cases == ["A"]
    assert finding.stage == "Lead"
    assert finding.case_details[0]["days_over"] == 6


def test_a_finished_case_is_never_flagged() -> None:
    """A and B both sat at their stage for weeks. A finished; B did not."""
    df = _log([
        ("A", "Lead", "2026-01-01", "Ruairi", None),
        ("A", "Paid", "2026-01-02", "Ruairi", None),
        ("B", "Lead", "2026-01-01", "Ruairi", None),
        ("C", "Lead", "2026-01-30", "Ruairi", None),      # sets the analysis date
    ])
    finding = _by_type(detect_case_findings(df, _CFG))["stage_sla_breach"]
    assert finding.affected_cases == ["B"]


def test_unowned_case_flags_a_blank_owner() -> None:
    df = _log([
        ("A", "Proposal", "2026-01-01", "", None),
        ("B", "Proposal", "2026-01-01", "Ruairi", None),
    ])
    finding = _by_type(detect_case_findings(df, _CFG))["unowned_case"]
    assert finding.affected_cases == ["A"]


def test_unrealised_value_sums_only_unbilled_cases() -> None:
    df = _log([
        ("A", "Proposal", "2026-01-01", "Ruairi", 5000.0),
        ("B", "Proposal", "2026-01-01", "Ruairi", 3000.0),
        ("C", "Proposal", "2026-01-01", "Ruairi", 9000.0),
        ("C", "Paid", "2026-01-05", "Ruairi", 9000.0),     # already realised
    ])
    finding = _by_type(detect_case_findings(df, _CFG))["unrealised_value"]
    assert finding.affected_cases == ["A", "B"]
    assert finding.metric_value == 8000.0
    assert "£8,000" in finding.title


def test_overloaded_owner_needs_to_exceed_the_limit_not_meet_it() -> None:
    rows = [(f"C{i}", "Proposal", "2026-01-01", "Aoife", None) for i in range(2)]
    assert "overloaded_owner" not in _by_type(detect_case_findings(_log(rows), _CFG))
    rows.append(("C9", "Proposal", "2026-01-01", "Aoife", None))
    finding = _by_type(detect_case_findings(_log(rows), _CFG))["overloaded_owner"]
    assert finding.subject == "Aoife"
    assert finding.metric_value == 3.0


def test_key_person_dependency_needs_someone_to_compare_against() -> None:
    solo = _log([(f"C{i}", "Proposal", "2026-01-01", "Aoife", None)
                 for i in range(5)])
    # One person doing 100% of a stage nobody else touches is not a finding —
    # there is no comparison to make.
    assert "key_person_dependency" not in _by_type(detect_case_findings(solo, _CFG))

    mixed = _log([*[(f"C{i}", "Proposal", "2026-01-01", "Aoife", None)
                    for i in range(4)],
                  ("C9", "Proposal", "2026-01-01", "Declan", None)])
    finding = _by_type(detect_case_findings(mixed, _CFG))["key_person_dependency"]
    assert finding.subject == "Aoife"
    assert finding.metric_value == 0.8


def test_no_rules_configured_means_no_case_findings() -> None:
    df = _log([("A", "Lead", "2026-01-01", "", None)])
    bare = {"stage_order": ["Lead"], "case_rules": {"stage_sla_days": {},
                                                    "default_stalled_days": 0,
                                                    "owner_load_limit": 0,
                                                    "key_person_min_share": 0,
                                                    "revenue_stages": []}}
    assert [f.type for f in detect_case_findings(df, bare)] == ["unowned_case"]


def test_findings_are_ordered_and_get_stable_ids() -> None:
    df = _log([
        ("A", "Lead", "2026-01-01", "", 1000.0),
        ("B", "Lead", "2026-01-01", "", 2000.0),
        ("C", "Proposal", "2026-01-01", "Aoife", 500.0),
    ])
    first = detect_case_findings(df, _CFG)
    second = detect_case_findings(df, _CFG)
    assert [f.id for f in first] == [f.id for f in second]
    assert [f.type for f in first] == [f.type for f in second]
    assert first[0].id == "CF001"


# ── The Northstar Advisory profile ───────────────────────────────────────────

def _advisory_log() -> pd.DataFrame:
    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    cfg = config.MESSY_PROFILES["advisory"]
    gt = json.loads(cfg["gt_mapping"].read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile="advisory", approved_at="2026-07-23T00:00:00+00:00",
        source_proposal_generated_at="2026-07-23T00:00:00+00:00",
        files=[ApprovedFileMapping(**f) for f in gt["files"]])
    rows, _ = read_mapped(cfg["dir"], approved)
    df = pd.DataFrame(rows)
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def test_advisory_drive_carries_the_money_column() -> None:
    """The optional canonical `value` field is what makes revenue-at-risk
    ranking possible without any SME-specific code."""
    df = _advisory_log()
    values = pd.to_numeric(df["value"], errors="coerce").dropna()
    assert len(values) == len(df)
    assert values.min() > 0


def test_advisory_stage_sla_breaches_cover_lead_and_proposal() -> None:
    """The commercial demo's specific claim: uncontacted leads and proposals
    stuck in approval are both caught by the stage_sla_breach rule. This is
    the one advisory-specific assertion the parametrized suite below does not
    carry, so it is kept here as its own test."""
    findings = detect_case_findings(_advisory_log(),
                                    config.MESSY_PROFILES["advisory"])
    stages = {f.stage for f in findings if f.type == "stage_sla_breach"}
    # Uncontacted leads, proposals stuck in approval, and delivered work not
    # yet invoiced — the three the commercial demo leans on.
    assert {"Lead", "Proposal"} <= stages


def test_advisory_key_person_is_a_real_concentration() -> None:
    findings = [f for f in detect_case_findings(
        _advisory_log(), config.MESSY_PROFILES["advisory"])
        if f.type == "key_person_dependency"]
    assert findings
    assert all(f.metric_value >= 0.6 for f in findings)
    assert {f.stage for f in findings} <= {"Delivery", "Client Review"}


def test_no_staff_names_survive_into_the_action_queue() -> None:
    """The privacy control, checked where it actually matters to a worker: the
    queue is the artefact that gets shared, exported and screenshotted, so no
    real name may appear anywhere in it — not in a title, not in evidence, not
    in the LLM payload."""
    import json as _json

    from actions.build import build_action_items
    from pipeline.normalise import normalise_foyle_events
    from scrub.anonymise import reset_actor_registry
    from synthetic.generate_messy_advisory import (CONSULTANTS, PRACTICE_MANAGER,
                                                   SALES_OWNERS)

    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    cfg = config.MESSY_PROFILES["advisory"]
    gt = _json.loads(cfg["gt_mapping"].read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile="advisory", approved_at="2026-07-23T00:00:00+00:00",
        source_proposal_generated_at="2026-07-23T00:00:00+00:00",
        files=[ApprovedFileMapping(**f) for f in gt["files"]])
    rows, _ = read_mapped(cfg["dir"], approved)

    reset_actor_registry()
    records = normalise_foyle_events(rows, source_type="messy")
    events = [e.to_dict() for r in records for e in r.events]
    df = pd.DataFrame(events)
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")

    items = build_action_items("advisory", df)
    blob = _json.dumps([i.model_dump() for i in items], ensure_ascii=False)
    for name in [*CONSULTANTS, *SALES_OWNERS, PRACTICE_MANAGER]:
        assert name not in blob, name
        assert name.split()[0] not in blob, name
    assert "[PERSON_" in blob, "the placeholders should still be there"


# ── The foyle profile's operational patterns ─────────────────────────────────

def _drive_log(profile: str) -> pd.DataFrame:
    """A profile's whole drive, read through its ground-truth mapping."""
    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    cfg = config.MESSY_PROFILES[profile]
    gt = json.loads(cfg["gt_mapping"].read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile=profile, approved_at="2026-07-31T00:00:00+00:00",
        source_proposal_generated_at="2026-07-31T00:00:00+00:00",
        files=[ApprovedFileMapping(**f) for f in gt["files"]])
    rows, _ = read_mapped(cfg["dir"], approved)
    df = pd.DataFrame(rows)
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _drive_gt(profile: str) -> dict:
    return json.loads(config.MESSY_PROFILES[profile]["gt_bottlenecks"]
                      .read_text(encoding="utf-8"))


# Every profile's drive must exercise the case rules, and each profile
# advertises a slightly different set — foyle and joinery carry no money
# column, so `unrealised_value` cannot fire for them and deliberately does
# not appear here.
_EXPECTED_CASE_FINDINGS = {
    "foyle": {"stage_sla_breach", "stalled_case", "unowned_case",
              "overloaded_owner"},
    "joinery": {"stage_sla_breach", "stalled_case", "unowned_case",
                "overloaded_owner"},
    "advisory": {"stage_sla_breach", "unowned_case", "unrealised_value",
                 "overloaded_owner", "key_person_dependency"},
}


@pytest.mark.parametrize("profile", PROFILES)
def test_every_drive_exercises_its_case_rules(profile: str) -> None:
    findings = detect_case_findings(_drive_log(profile),
                                    config.MESSY_PROFILES[profile])
    types = {f.type for f in findings}
    assert _EXPECTED_CASE_FINDINGS[profile] <= types


@pytest.mark.parametrize("profile", PROFILES)
def test_flagged_cases_are_a_strict_subset_of_the_parked_ones(profile: str) -> None:
    """The circularity guard, on every profile: the generator records where it
    parked each case, the rules decide independently whether that is a problem
    yet — so some parked cases must go unflagged."""
    gt = _drive_gt(profile)
    parked = {c for cases in gt["operational_intent"]["parked_at"].values()
              for c in cases}
    findings = detect_case_findings(_drive_log(profile),
                                    config.MESSY_PROFILES[profile])
    flagged = {c for f in findings if f.type == "stage_sla_breach"
               for c in f.affected_cases}
    assert flagged, "the parked cases should produce SLA breaches"
    assert flagged < parked


@pytest.mark.parametrize("profile", PROFILES)
def test_unowned_matches_the_seeded_intent(profile: str) -> None:
    gt = _drive_gt(profile)
    findings = _by_type(detect_case_findings(
        _drive_log(profile), config.MESSY_PROFILES[profile]))
    assert (findings["unowned_case"].affected_cases
            == sorted(gt["operational_intent"]["unowned"]))


@pytest.mark.parametrize("profile", ["foyle", "joinery"])
def test_profiles_without_a_money_column_report_no_unrealised_value(
        profile: str) -> None:
    """Stated as a decision, not a silent gap: those two drives carry no value
    column, so the commercial rule has nothing to work from. Adding one would
    move the mapping ground truth, which is why it was not done."""
    findings = detect_case_findings(_drive_log(profile),
                                    config.MESSY_PROFILES[profile])
    assert "unrealised_value" not in {f.type for f in findings}


@pytest.mark.parametrize("profile", PROFILES)
def test_every_profile_declares_the_action_layer_config(profile: str) -> None:
    """A new SME is a config block, not code — so every profile must carry the
    same shape of block, and none may be missing one."""
    cfg = config.MESSY_PROFILES[profile]
    assert cfg["actions"]["case_noun"]
    assert cfg["actions"]["workflow"]
    assert cfg["actions"]["impact"]["avg_case_value"] > 0
    rules = cfg["case_rules"]
    assert rules["terminal_stages"]
    assert set(rules["stage_sla_days"]) <= set(cfg["stage_order"])
    assert set(rules["terminal_stages"]) <= set(cfg["stage_order"])
    assert set(rules["revenue_stages"]) <= set(cfg["stage_order"])
