"""Longitudinal replay — time as the experiment variable.

    python -m eval.replay --profile foyle              # offline diagnosis (free)
    python -m eval.replay --profile foyle --llm        # Claude diagnosis per finding

Replays the stream drive (synthetic/generate_stream.py) tick by tick through
the UNCHANGED pipeline core: ingest the snapshot via the ground-truth mapping
(isolating this eval from mapping quality, exactly as eval/score_detection
does), run dynamic detection, retrieve + diagnose per finding, put every
suggestion through a SIMULATED Gate-2 approver, feed approvals to the
learning loop, and score the tick. Two longitudinal claims come out of the
log:

    detection tracks drift — per-tick P/R/F1 against a per-tick ground truth
        that grows as injected patterns become visible in the data
    learning compounds, but only on proof — an approved fix becomes a tracked
        intervention with a baseline; at a LATER tick it is measured against
        the analysis of that tick, and only if the measurement shows real
        improvement does it enter `sme_resolutions` (source="learned")

The second claim is the corrected one. The earlier build embedded a fix the
moment it was approved, which treated a decision to try something as evidence
it worked. Each tick record now carries both curves: `lifecycle.validated`
(what the outcome-gated loop trusts) and `lifecycle.approved_unmeasured`
(what the old approval-gated loop would have trusted by the same tick), so the
difference is a result rather than a silent behaviour change.

HONESTY NOTES (the write-up must repeat both):
  1. The tick-by-tick approver is an ORACLE, not a human — it approves a
     suggested fix when the majority of the cases behind it are genuinely
     affected per ground truth. It stands in for the domain expert so nine
     weeks can replay in minutes; the real Gate 2 is the dashboard demo.
  2. The stream is a RECORDING, not a counterfactual: an intervention approved
     at tick t cannot actually change what tick t+1 contains. A validated
     outcome here means "the metric genuinely moved between two real
     analyses", which exercises and evidences the measurement machinery — it
     does not establish that the intervention caused the movement. Causal
     evidence needs a live deployment, which is outside this artifact's scope.

Oracle decisions are logged to outputs/replay_decisions_<profile>.jsonl, never
to the dashboard's decisions.jsonl. Learned entries live in their own file
(outputs/replay_learned_<profile>.json, ids RES-RPL-*), approvals awaiting
proof in outputs/replay_pending_<profile>.json, and the interventions
themselves in outputs/replay_interventions_<profile>.json — so the dashboard's
real learned resolutions are never touched.
By default each run starts fresh: the resolution collection is rebuilt from
the seeded corpora (every profile, so cross-profile distractors stay in) and
the replay-learned file is removed — without this, fixes learned in a
previous run would leak into tick 1 and fake the curve. `--no-fresh` skips
the reset (dashboard-learned embeddings dropped by a fresh run are re-upserted
by the next real `pipeline.learn` call — the file of record is untouched).

Per-tick metrics append to outputs/replay_<profile>.jsonl (first line = run
meta); eval/plot_replay.py turns the log into the dissertation figures.

Runs as its own process and loads chroma + torch in-process (same pattern as
pipeline.learn); it never touches parquet, so the pyarrow constraint is moot.
"""

from __future__ import annotations

import os

# Same thread-pinning as pipeline/embed.py — must precede the native libs.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Silence chroma's posthog telemetry (its capture() signature drifted and spams
# "Failed to send telemetry event" on every collection call).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
from actions import lifecycle
from actions.execute import create_intervention
from actions.models import ActionItem, AnalysisSnapshot, make_snapshot_id
from actions.outcome import compare
from audit.schemas import ApprovedFileMapping, ApprovedMapping
from detection.detect import DetectedBottleneck
from detection.dynamic import detect_dynamic
from eval.score_detection import TYPES, _prf
from pipeline import learn
from readers.mapped_reader import read_mapped


def replay_learned_path(profile: str) -> Path:
    return config.OUTPUTS / f"replay_learned_{profile}.json"


def replay_pending_path(profile: str) -> Path:
    return config.OUTPUTS / f"replay_pending_{profile}.json"


def replay_interventions_path(profile: str) -> Path:
    return config.OUTPUTS / f"replay_interventions_{profile}.json"


def replay_decisions_path(profile: str) -> Path:
    return config.OUTPUTS / f"replay_decisions_{profile}.jsonl"


def _load_stream(profile: str) -> tuple[dict, ApprovedMapping]:
    gt_path = config.stream_gt_path(profile)
    if not gt_path.exists():
        raise SystemExit(f"{gt_path.name} missing — run "
                         f"synthetic/generate_stream.py --profile {profile} first")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile=profile, approved_at="2026-01-01T00:00:00+00:00",
        source_proposal_generated_at="2026-01-01T00:00:00+00:00",
        files=[ApprovedFileMapping(**f) for f in gt["mapping"]["files"]],
    )
    return gt, approved


def _tick_events(profile: str, tick: int, approved: ApprovedMapping) -> pd.DataFrame:
    rows, _ = read_mapped(config.stream_dir(profile) / f"tick_{tick:02d}", approved)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def fresh_start(profile: str) -> None:
    """Reproducibility reset: seeded corpora only, no replay-learned leftovers."""
    from pipeline.embed_resolutions import (embed_resolutions,
                                            reset_resolutions_collection)

    reset_resolutions_collection()
    for p in sorted(config.MESSY_PROFILES):
        n = embed_resolutions(p)
        print(f"[fresh] {n} seeded {p} resolutions embedded")
    for path in (replay_learned_path(profile), replay_pending_path(profile),
                 replay_interventions_path(profile)):
        if path.exists():
            path.unlink()
    print("[fresh] note: dashboard-learned embeddings are re-upserted by the "
          "next real pipeline.learn call; the learned file itself is untouched")


def oracle_decide(bn: DetectedBottleneck, truth_by_type: dict[str, set[str]]) -> bool:
    """The simulated Gate-2 reviewer: approve when the majority of the cases
    behind the finding are genuinely affected per the tick's ground truth."""
    overlap = len(set(bn.affected_cases) & truth_by_type.get(bn.type, set()))
    return overlap * 2 > len(bn.affected_cases)


def _retrieval_stats(bn: DetectedBottleneck, retrieved: list[dict]) -> dict:
    """Learned-fix visibility inside this finding's top-k retrieval."""
    learned = [(i + 1, r) for i, r in enumerate(retrieved)
               if str(r.get("source", "")).endswith("learned")]
    relevant = [rank for rank, r in learned
                if r.get("bottleneck_type") == bn.type]
    return {
        "learned_in_topk": bool(learned),
        "learned_rank": learned[0][0] if learned else None,
        "relevant_learned_in_topk": bool(relevant),
    }


def _decision_record(profile: str, bn: DetectedBottleneck, diag, approve: bool,
                     tick: int, cutoff: str) -> dict:
    slug = bn.stage.lower().replace(" ", "-")
    return {
        # No tick in the id: the same finding recurring next week replays the
        # SAME decision, and the learned-file dedup keeps a single entry —
        # a fix is approved once, not re-approved weekly.
        "decision_id": f"replay-{profile}-{bn.type}-{slug}",
        "action": "approve" if approve else "reject",
        "decided_at": f"{cutoff}T17:00:00+00:00",
        "tick": tick,
        "original_fix_snapshot": {
            "summary": diag.suggested_fix.summary,
            "steps": diag.suggested_fix.steps,
            "rationale": diag.suggested_fix.rationale,
        },
        "case_snapshot": {"type": bn.type, "stage": bn.stage,
                          "description": diag.diagnosis},
    }


def _record_pending(profile: str, decision: dict) -> dict | None:
    """Log the oracle's approval to the replay's PENDING store — the audit
    trail. Nothing here is embedded, so nothing here is retrievable. Its size
    over time is the curve the old approval-gated loop would have produced,
    kept alongside the new one for direct comparison in the write-up."""
    path = replay_pending_path(profile)
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if any(e.get("decision_id") == decision["decision_id"] for e in entries):
        return None
    entry = learn.build_resolution(profile, decision, len(entries))
    entry["resolution_id"] = f"RES-PND-{profile[:3].upper()}-{len(entries) + 1:03d}"
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return entry


def _promote_validated(profile: str, intervention) -> dict | None:
    """A validated-effective intervention becomes retrievable knowledge, in
    the replay's own file with RES-RPL-* ids so the dashboard's real learned
    entries (RES-LRN-*) are never shadowed in chroma."""
    if not intervention.is_trusted_knowledge:
        return None
    path = replay_learned_path(profile)
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if any(e.get("intervention_id") == intervention.intervention_id
           for e in entries):
        return None
    entry = learn.build_resolution_from_intervention(profile, intervention,
                                                     len(entries))
    entry["resolution_id"] = f"RES-RPL-{profile[:3].upper()}-{len(entries) + 1:03d}"
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return entry


def finding_key(bn: DetectedBottleneck) -> str:
    """Stable across ticks so a later snapshot can find the same finding."""
    return f"{bn.type}::{bn.stage.strip().lower()}::{bn.metric_label}"


def tick_snapshot(profile: str, tick: int, cutoff: str, df: pd.DataFrame,
                  findings: list[DetectedBottleneck]) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        snapshot_id=make_snapshot_id(profile, f"{cutoff}-tick{tick:02d}"),
        profile=profile, taken_at=f"{cutoff}T17:00:00+00:00",
        label=f"tick_{tick:02d}",
        case_count=int(df["case_id"].nunique()) if not df.empty else 0,
        event_count=int(len(df)),
        metrics={finding_key(bn): float(bn.metric_value) for bn in findings},
        affected_counts={finding_key(bn): bn.affected_count for bn in findings},
        present_keys=[finding_key(bn) for bn in findings])


def _action_item(profile: str, bn: DetectedBottleneck, diag) -> ActionItem:
    """The queue row the oracle is acting on — built through the same models
    the dashboard uses, so the replay exercises the real lifecycle rather than
    a parallel one."""
    from actions.models import make_action_id

    key = finding_key(bn)
    return ActionItem(
        action_id=make_action_id(profile, key), profile=profile,
        finding_key=key, finding_type=bn.type, title=f"{bn.stage} {bn.type}",
        summary=diag.diagnosis, stage=bn.stage,
        affected_case_ids=list(bn.affected_cases),
        metric_label=bn.metric_label, metric_value=bn.metric_value,
        recommended_action=diag.suggested_fix.summary,
        action_steps=list(diag.suggested_fix.steps))


def oracle_review(profile: str, interventions: list, snapshot: AnalysisSnapshot,
                  *, at: str) -> dict:
    """The simulated outcome review: measure every completed intervention
    against this tick's analysis, then let the oracle confirm the reading.

    The oracle only ever CONFIRMS what the measurement says — it never
    overrules it — so a fix becomes knowledge on the strength of the data, not
    the approver's opinion. `effective is None` (movement inside the noise
    band) leaves the intervention in review for a later tick, which is exactly
    what a cautious human would do."""
    stats = {"reviewed": 0, "validated": 0, "ineffective": 0, "still_unclear": 0}
    for intervention in interventions:
        if intervention.status not in ("completed", "outcome_review"):
            continue
        if intervention.baseline_snapshot_id == snapshot.snapshot_id:
            continue
        outcome = compare(intervention, snapshot, at=at)
        intervention.outcome = outcome
        if intervention.status == "completed":
            lifecycle.transition(intervention, "outcome_review", actor="oracle",
                                 note=f"measured at {snapshot.label}", at=at)
        stats["reviewed"] += 1

        if outcome.effective is None:
            stats["still_unclear"] += 1
            continue
        outcome.validated_by_human = True
        outcome.validated_at = at
        outcome.validated_by = "oracle"
        lifecycle.transition(intervention,
                             "validated" if outcome.effective else "ineffective",
                             actor="oracle", note=outcome.effective_reason, at=at)
        stats["validated" if outcome.effective else "ineffective"] += 1
    return stats


def run_tick(profile: str, tick_meta: dict, approved: ApprovedMapping, *,
             llm: bool, decisions_out: list[dict],
             interventions: list) -> dict:
    from bridge.export_messy import _estimated_cost
    from pipeline.diagnose import diagnose, offline_diagnosis, retrieve_resolutions

    t, cutoff = tick_meta["tick"], tick_meta["cutoff"]
    at = f"{cutoff}T17:00:00+00:00"
    truth = {k: set(tick_meta["bottlenecks"][k]) for k in TYPES}
    df = _tick_events(profile, t, approved)
    stage_order = config.MESSY_PROFILES[profile]["stage_order"]
    findings = [] if df.empty else detect_dynamic(df, stage_order)

    detected = {k: set() for k in TYPES}
    for bn in findings:
        detected[bn.type] |= set(bn.affected_cases)
    detection = {k: _prf(detected[k], truth[k]) for k in TYPES}
    macro_f1 = round(sum(detection[k]["f1"] for k in TYPES) / len(TYPES), 3)

    # Outcome review comes FIRST: interventions approved at earlier ticks are
    # measured against this tick's analysis, and only the ones that survive
    # that measurement are embedded — so a fix can influence retrieval no
    # earlier than the tick after it was approved, and only if it worked.
    snapshot = tick_snapshot(profile, t, cutoff, df, findings)
    review_stats = oracle_review(profile, interventions, snapshot, at=at)
    promoted = 0
    for intervention in interventions:
        if _promote_validated(profile, intervention):
            promoted += 1
    if promoted:
        learn.embed_learned(profile, path=replay_learned_path(profile))

    per_finding: list[dict] = []
    approved_n = rejected_n = pending_new = 0
    known_decisions = {i.decision_id for i in interventions}
    for bn in findings:
        retrieved = retrieve_resolutions(f"{bn.stage} {bn.type} bottleneck", profile)
        stats = _retrieval_stats(bn, retrieved)
        bn_dict = {**asdict(bn), "affected_count": bn.affected_count,
                   "evidence_excerpts": bn.example_refs}
        diag = (diagnose(bn_dict, profile, retrieved=retrieved) if llm
                else offline_diagnosis(bn_dict, retrieved))
        approve = oracle_decide(bn, truth)
        decision = _decision_record(profile, bn, diag, approve, t, cutoff)
        decisions_out.append(decision)
        if approve:
            approved_n += 1
            if _record_pending(profile, decision):
                pending_new += 1
            if decision["decision_id"] not in known_decisions:
                # The oracle stands in for a worker who approves, owns the work
                # and finishes it within the week — so the intervention is
                # ready to be MEASURED at the next tick, not before.
                intervention = create_intervention(
                    _action_item(profile, bn, diag), snapshot=snapshot,
                    owner="oracle", decision_id=decision["decision_id"],
                    actor="oracle", at=at)
                for status in ("assigned", "in_progress", "completed"):
                    lifecycle.transition(intervention, status, actor="oracle", at=at)
                interventions.append(intervention)
                known_decisions.add(decision["decision_id"])
        else:
            rejected_n += 1
        per_finding.append({"id": bn.id, "type": bn.type, "stage": bn.stage,
                            "cases": bn.affected_count,
                            "confidence": diag.confidence, **stats})

    lp = replay_learned_path(profile)
    corpus = len(json.loads(lp.read_text(encoding="utf-8"))) if lp.exists() else 0
    pp = replay_pending_path(profile)
    pending_corpus = len(json.loads(pp.read_text(encoding="utf-8"))) if pp.exists() else 0
    with_learned = [f for f in per_finding if f["learned_in_topk"]]
    cost = sum((_estimated_cost(bn, profile) or {}).get("amount", 0)
               for bn in findings)
    return {
        "record": "tick", "tick": t, "cutoff": cutoff,
        "cases_visible": tick_meta["cases_visible"],
        "events_visible": tick_meta["events_visible"],
        "detection": {**detection, "macro_f1": macro_f1},
        "findings": len(findings),
        "gate": {"approved": approved_n, "rejected": rejected_n,
                 "learned_new": promoted, "pending_new": pending_new,
                 **review_stats},
        "lifecycle": {
            "open_interventions": sum(
                1 for i in interventions
                if i.status not in ("validated", "ineffective", "rejected",
                                    "dismissed")),
            "validated": sum(1 for i in interventions if i.status == "validated"),
            "ineffective": sum(1 for i in interventions
                               if i.status == "ineffective"),
            # What the OLD approval-gated loop would have trusted by now — kept
            # so the write-up can put the two curves side by side.
            "approved_unmeasured": pending_corpus,
        },
        "retrieval": {
            "learned_hit_rate": (round(len(with_learned) / len(per_finding), 3)
                                 if per_finding else None),
            "relevant_learned_hit_rate":
                (round(sum(f["relevant_learned_in_topk"] for f in per_finding)
                       / len(per_finding), 3) if per_finding else None),
            "mean_learned_rank":
                (round(sum(f["learned_rank"] for f in with_learned)
                       / len(with_learned), 2) if with_learned else None),
            "learned_corpus_size": corpus,
        },
        "modelled_cost": cost,
        "per_finding": per_finding,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay the stream drive tick by tick and log per-tick metrics")
    ap.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    ap.add_argument("--llm", action="store_true",
                    help="Claude diagnosis per finding (default: offline templates)")
    ap.add_argument("--no-fresh", dest="fresh", action="store_false",
                    help="skip the reproducibility reset of the resolution store")
    args = ap.parse_args()

    if args.llm:
        from pipeline.diagnose import load_dotenv
        load_dotenv()

    gt, approved = _load_stream(args.profile)
    if args.fresh:
        fresh_start(args.profile)

    decisions: list[dict] = []
    interventions: list = []
    records = [{"record": "meta", "profile": args.profile,
                "mode": "llm" if args.llm else "offline",
                "fresh": args.fresh, "stream_seed": gt["seed"],
                "learning_gate": "outcome_validated",
                "started_at": datetime.now(timezone.utc).isoformat()}]
    for tick_meta in gt["ticks"]:
        rec = run_tick(args.profile, tick_meta, approved, llm=args.llm,
                       decisions_out=decisions, interventions=interventions)
        records.append(rec)
        r, lc = rec["retrieval"], rec["lifecycle"]
        print(f"tick {rec['tick']}: macroF1 {rec['detection']['macro_f1']:.3f}  "
              f"findings {rec['findings']}  "
              f"gate +{rec['gate']['approved']}/-{rec['gate']['rejected']}  "
              f"validated {lc['validated']} (unmeasured-if-old-rule "
              f"{lc['approved_unmeasured']})  "
              f"learned-hit {r['learned_hit_rate']}  corpus {r['learned_corpus_size']}")

    log = config.replay_log_path(args.profile)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                           for r in records), encoding="utf-8")
    replay_decisions_path(args.profile).write_text(
        "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in decisions),
        encoding="utf-8")
    replay_interventions_path(args.profile).write_text(
        json.dumps([i.model_dump() for i in interventions],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {log.relative_to(config.ROOT)}  ({len(records) - 1} ticks, "
          f"{len(decisions)} gate decisions, {len(interventions)} interventions)")


if __name__ == "__main__":
    main()
