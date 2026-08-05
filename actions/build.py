"""Findings -> evidence-backed action items.

This is the join between the analytical half of the system (which stages are
broken, which cases are stuck, which data is messy) and the operational half
(what a worker should do about it today).

Three sources feed the queue, and they stay distinguishable all the way to the
UI because they need different handling:

    detection/dynamic.py    structural stage findings  -> process interventions
    detection/case_rules.py case-level findings        -> case actions
    remediation + mapping   data findings              -> data-quality work

Confidence is reported in two independent numbers, because they mean different
things. `detection_confidence` is how sure the system is that the finding is
real. `data_quality_confidence` is how much the underlying spreadsheet data can
be trusted at all — a queue built on a drive full of blank owners and
unparseable dates should say so rather than quietly ranking on sand.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd

import config
from actions import templates
from actions.impact import assumptions, data_quality_impact
from actions.impact import build_impact
from actions.models import (ActionItem, AnalysisSnapshot, EvidenceReference,
                            make_action_id, make_snapshot_id)
from detection.case_rules import CaseFinding, analysis_date, detect_case_findings
from detection.dynamic import detect_dynamic

# Deterministic rules are near-certain by construction; a structural detection
# is strong but inferential; an LLM anomaly is a suggestion, not a finding.
_BASE_CONFIDENCE = {
    "delay": 0.85, "repetition": 0.9, "rework": 0.9,
    "stage_sla_breach": 0.95, "stalled_case": 0.95, "unowned_case": 0.99,
    "unrealised_value": 0.95, "overloaded_owner": 0.95,
    "key_person_dependency": 0.9, "anomaly": 0.4,
    "messy_status_values": 0.99, "stale_duplicate_file": 0.8,
}


def _canon(text: str) -> str:
    return str(text or "").strip().lower()


def _iso(d: date) -> str:
    return d.isoformat()


def _as_of_date(df: pd.DataFrame, as_of=None) -> date:
    ts = analysis_date(df, as_of)
    return ts.date() if ts is not None else date.today()


# ── Data-quality confidence ──────────────────────────────────────────────────

def data_quality_confidence(df: pd.DataFrame) -> tuple[float, str]:
    """How trustworthy is the data this queue is built on?

    Three cheap, honest signals: events with no owner, events whose timestamp
    would not parse, and cases with only a single event (usually a sign of a
    sheet that was abandoned rather than a workflow that finished). Returned
    with a sentence explaining the number, because an unexplained confidence
    score is worse than none."""
    if df.empty:
        return 0.0, "no events in the log"
    n = len(df)
    unowned = int(df["actor"].isna().sum() + (df["actor"].astype(str).str.strip() == "").sum())
    undated = int(pd.to_datetime(df["ts"], errors="coerce").isna().sum())
    thin = int((df.groupby("case_id").size() == 1).sum())
    cases = max(1, df["case_id"].nunique())

    score = 1.0 - (0.4 * unowned / n) - (0.4 * undated / n) - (0.2 * thin / cases)
    score = round(max(0.0, min(1.0, score)), 2)
    return score, (f"{unowned}/{n} events with no owner, {undated}/{n} with an "
                   f"unreadable date, {thin}/{cases} cases with a single event")


# ── Evidence ─────────────────────────────────────────────────────────────────

def _metric_evidence(label: str, value: float, detail: str) -> EvidenceReference:
    return EvidenceReference(kind="metric", label=label, metric_label=label,
                             metric_value=float(value), detail=detail)


def _case_evidence(case_details: list[dict], limit: int = 8) -> list[EvidenceReference]:
    """One reference per affected case, capped — the UI expands the rest from
    `affected_case_ids`. Each carries the spreadsheet row it came from."""
    out: list[EvidenceReference] = []
    for d in case_details[:limit]:
        bits = []
        if d.get("days_waiting") is not None:
            bits.append(f"waiting {d['days_waiting']} days")
        if d.get("days_idle") is not None:
            bits.append(f"idle {d['days_idle']} days")
        if d.get("days_over") is not None:
            bits.append(f"{d['days_over']} days over the limit")
        if d.get("value") is not None:
            bits.append(f"value {d['value']:,.0f}")
        if d.get("owner"):
            bits.append(f"owner {d['owner']}")
        if d.get("stage"):
            bits.append(f"at {d['stage']}")
        ref = d.get("source_ref")
        if ref:
            out.append(EvidenceReference.from_source_ref(
                ref, label=str(d.get("case_id", "")),
                case_id=d.get("case_id"), detail="; ".join(bits) or None))
        else:
            out.append(EvidenceReference(
                kind="case_list", label=str(d.get("case_id", "")),
                case_id=d.get("case_id"), detail="; ".join(bits) or None))
    return out


def _ref_evidence(refs: list[str], label: str) -> list[EvidenceReference]:
    return [EvidenceReference.from_source_ref(r, label=label)
            for r in (refs or []) if r]


# ── Item construction ────────────────────────────────────────────────────────

def _make_item(profile: str, *, finding_key: str, finding_type: str,
               stage: str, title: str, summary: str,
               affected: list[str], evidence: list[EvidenceReference],
               metric_label: str | None, metric_value: float | None,
               case_details: list[dict], as_of: date,
               subject: str | None = None,
               confidence: float | None = None,
               dq_confidence: float | None = None,
               source_records: list[str] | None = None,
               generated_by: str = "detector",
               llm_payload: dict | None = None,
               retrieved_resolutions: list[dict] | None = None,
               impact=None) -> ActionItem:
    tpl = templates.template_for(profile, finding_type)
    category = tpl["category"]
    fmt = {"stage": stage or "this stage", "count": len(affected) or 1,
           "metric": metric_value if metric_value is not None else 0,
           "subject": subject or "this person",
           "case_noun": templates.case_noun(profile)}

    now = as_of.isoformat()
    return ActionItem(
        action_id=make_action_id(profile, finding_key),
        profile=profile,
        finding_key=finding_key,
        finding_type=finding_type,
        title=title,
        summary=summary,
        workflow=templates.workflow_name(profile),
        stage=stage,
        affected_case_ids=sorted(affected),
        evidence=evidence,
        metric_label=metric_label,
        metric_value=metric_value,
        detection_confidence=(confidence if confidence is not None
                              else _BASE_CONFIDENCE.get(finding_type, 0.7)),
        data_quality_confidence=dq_confidence,
        impact=impact if impact is not None else build_impact(
            profile, finding_type, affected=len(affected),
            metric=float(metric_value or 0), case_details=case_details),
        recommended_action=templates.render(tpl["action"], **fmt),
        action_steps=[templates.render(s, **fmt) for s in tpl.get("steps", [])],
        action_category=category,
        action_template=tpl.get("template_id"),
        owner=templates.default_owner(profile, category),
        due_date=_iso(as_of + timedelta(days=templates.due_days(profile, category))),
        status="proposed",
        created_at=now,
        updated_at=now,
        source_records=source_records or [],
        generated_by=generated_by,
        llm_payload=llm_payload,
        retrieved_resolutions=retrieved_resolutions or [],
    )


def _structural_items(profile: str, df: pd.DataFrame, as_of: date,
                      dq: float, diagnosis_by_id: dict, is_legacy_export: bool) -> list[ActionItem]:
    stage_order = config.MESSY_PROFILES[profile]["stage_order"]
    items: list[ActionItem] = []

    for bn in detect_dynamic(df, stage_order):
        key = f"{bn.type}::{_canon(bn.stage)}::{bn.metric_label}"
        diag = diagnosis_by_id.get(key) or {}
        if not diag and is_legacy_export:
            diag = diagnosis_by_id.get(bn.id) or {}
        case_details = [{"case_id": c} for c in bn.affected_cases]
        evidence = [
            _metric_evidence(bn.metric_label, bn.metric_value,
                             f"measured across {bn.affected_count} case(s) at {bn.stage}"),
            *_ref_evidence(bn.example_refs, f"{bn.stage} source row"),
            *_case_evidence(case_details),
        ]
        summary = (diag.get("description")
                   or f"{bn.affected_count} case(s) show the {bn.type} pattern "
                      f"at {bn.stage} ({bn.metric_label}={bn.metric_value:g}).")
        item = _make_item(
            profile, finding_key=key, finding_type=bn.type, stage=bn.stage,
            title=diag.get("title") or f"{bn.stage}: {bn.type} affecting "
                                       f"{bn.affected_count} case(s)",
            summary=summary, affected=bn.affected_cases, evidence=evidence,
            metric_label=bn.metric_label, metric_value=bn.metric_value,
            case_details=case_details, as_of=as_of, dq_confidence=dq,
            confidence=diag.get("confidence"),
            source_records=list(bn.example_refs),
            llm_payload=diag.get("llm_payload"),
            retrieved_resolutions=diag.get("retrieved_resolutions") or [])
        # A diagnosis that came back from the RAG agent replaces the authored
        # wording, but never the category — routing is the system's decision.
        fix = diag.get("suggested_fix") or {}
        if fix.get("summary"):
            item.recommended_action = fix["summary"]
            item.action_steps = list(fix.get("steps") or item.action_steps)
        items.append(item)
    return items


def _is_masked(name: str | None) -> bool:
    """Staff names are replaced with placeholders during ingestion, so a
    finding about a PERSON names a placeholder, not a colleague."""
    text = str(name or "")
    return text.startswith("[") and text.endswith("]")


_MASK_NOTE = (" The name is masked in the analysis store — open one of the "
              "listed source rows in the original spreadsheet to see who it is.")


def _case_rule_items(profile: str, df: pd.DataFrame, as_of: date,
                     dq: float) -> list[ActionItem]:
    cfg = config.MESSY_PROFILES[profile]
    items: list[ActionItem] = []

    for finding in detect_case_findings(df, cfg, as_of=as_of):
        subject_key = f"::{_canon(finding.subject)}" if finding.subject else ""
        key = (f"{finding.type}::{_canon(finding.stage)}::"
               f"{finding.metric_label}{subject_key}")
        evidence = [
            _metric_evidence(finding.metric_label, finding.metric_value,
                             finding.detail),
            *_case_evidence(finding.case_details),
        ]
        summary = finding.detail
        if _is_masked(finding.subject):
            summary += _MASK_NOTE
        items.append(_make_item(
            profile, finding_key=key, finding_type=finding.type,
            stage=finding.stage, title=finding.title, summary=summary,
            affected=finding.affected_cases, evidence=evidence,
            metric_label=finding.metric_label, metric_value=finding.metric_value,
            case_details=finding.case_details, as_of=as_of, dq_confidence=dq,
            subject=finding.subject, source_records=list(finding.example_refs),
            generated_by="case_rule"))
    return items


def _data_quality_items(profile: str, as_of: date, dq: float) -> list[ActionItem]:
    """Data findings from artefacts the pipeline has already produced: the
    remediation plan (messy status values) and the audit agent's mess report
    (stale/overlapping copies). No new scanning — these are already known."""
    items: list[ActionItem] = []
    a = assumptions(profile)

    plan_path = config.OUTPUTS / f"ui_remediation_{profile}.json"
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            plan = {}
        messy = sum(v["count"] for f in plan.get("files", [])
                    for v in f.get("value_map", [])
                    if v["original"].strip() != v["canonical"])
        if messy:
            files = [f["filename"] for f in plan.get("files", [])]
            evidence = [
                _metric_evidence("inconsistent_status_cells", messy,
                                 "cells whose written status differs from the "
                                 "controlled vocabulary"),
                *[EvidenceReference(kind="source_row", label=f, source_file=f)
                  for f in files[:5]],
            ]
            items.append(_make_item(
                profile, finding_key="data_quality::status_values::cells",
                finding_type="messy_status_values",
                stage=", ".join(files[:2]),
                title=f"{messy} status cells are written {len(files)} different ways",
                summary=("Staff type the same status a dozen ways. Reports and "
                         "filters silently miss rows because of it."),
                affected=[], evidence=evidence,
                metric_label="inconsistent_status_cells", metric_value=float(messy),
                case_details=[], as_of=as_of, dq_confidence=dq,
                source_records=files,
                impact=data_quality_impact(messy, a)))

    proposal_path = config.ui_mapping_proposal_path(profile)
    if proposal_path.exists():
        try:
            proposal = json.loads(proposal_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            proposal = {}
        mess = proposal.get("mess_report") or {}
        overlaps = list(mess.get("duplicates", [])) + list(mess.get("overlaps", []))
        if overlaps:
            items.append(_make_item(
                profile, finding_key="data_quality::stale_copies::files",
                finding_type="stale_duplicate_file", stage="the drive",
                title=f"{len(overlaps)} overlapping or duplicated file(s) on the drive",
                summary=("More than one file covers the same work. Whichever one "
                         "somebody opens first becomes the truth for that day."),
                affected=[],
                evidence=[EvidenceReference(kind="excerpt",
                                            label="Audit mess report", detail=o)
                          for o in overlaps[:6]],
                metric_label="overlapping_files",
                metric_value=float(len(overlaps)),
                case_details=[], as_of=as_of, dq_confidence=dq))
    return items


# ── Entry points ─────────────────────────────────────────────────────────────

def build_action_items(profile: str, df: pd.DataFrame, *,
                       cases: list[dict] | None = None,
                       as_of=None) -> list[ActionItem]:
    """The full queue for one analysis run, unranked and unmerged.

    `cases` is the existing ui_cases.json export, used only to enrich structural
    items with the RAG diagnosis wording and to carry the anomaly cards across.
    The queue is complete without it — the export is an enrichment, not a
    dependency."""
    as_of_date = _as_of_date(df, as_of)
    dq, _ = data_quality_confidence(df)
    cases = cases or []
    diagnosis_by_id = {}
    has_finding_key = any(c.get("finding_key") for c in cases
                          if c.get("type") != "anomaly")
    is_legacy_export = not has_finding_key

    for c in cases:
        if c.get("type") == "anomaly":
            continue
        if c.get("case_id"):
            diagnosis_by_id[c["case_id"]] = c        # legacy exports
        if c.get("finding_key"):
            diagnosis_by_id[c["finding_key"]] = c    # content key wins

    return [
        *_structural_items(profile, df, as_of_date, dq, diagnosis_by_id, is_legacy_export),
        *_case_rule_items(profile, df, as_of_date, dq),
        *_data_quality_items(profile, as_of_date, dq),
    ]


def build_snapshot(profile: str, df: pd.DataFrame, items: list[ActionItem],
                   *, label: str = "", as_of=None) -> AnalysisSnapshot:
    """The measurable state of this run, keyed by finding_key so a later run
    can compare like with like."""
    taken_at = _as_of_date(df, as_of).isoformat()
    return AnalysisSnapshot(
        snapshot_id=make_snapshot_id(profile, taken_at),
        profile=profile,
        taken_at=taken_at,
        label=label,
        case_count=int(df["case_id"].nunique()) if not df.empty else 0,
        event_count=int(len(df)),
        metrics={i.finding_key: float(i.metric_value)
                 for i in items if i.metric_value is not None},
        affected_counts={i.finding_key: len(i.affected_case_ids) for i in items},
        present_keys=[i.finding_key for i in items],
    )
