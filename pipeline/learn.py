"""Learning loop — outcome-gated. Only fixes that were MEASURED to work become
retrievable organisational knowledge.

    python -m pipeline.learn --profile foyle --decision-json <path>   # record an approval
    python -m pipeline.learn --profile foyle --promote                # promote validated ones
    python -m pipeline.learn --profile foyle --migrate-legacy         # one-off, see below

What changed and why
--------------------
The previous version wrote an approved Gate-2 fix straight into the
`sme_resolutions` collection the diagnosis agent retrieves from. That treated a
human saying "yes, try this" as evidence the thing worked. It does not, and a
RAG store full of untested ideas quietly degrades every future diagnosis.

There are now two stores, and the gap between them is the whole point:

    data/learned/pending_resolutions_<p>.json   every approval, forever.
        The audit trail. Rejected, ineffective and merely-approved-but-unproven
        entries all live here. NEVER embedded, never retrievable as advice.

    data/learned/learned_resolutions_<p>.json   trusted knowledge.
        Only interventions that reached `validated` with an outcome measured
        against a LATER analysis and confirmed by a human. These are embedded
        into `sme_resolutions` alongside the seeded corpus.

The gate is `Intervention.is_trusted_knowledge` (actions/models.py) — one
predicate, checked in one place, so the rule cannot drift.

Idempotency: pending dedups on decision_id, trusted dedups on
intervention_id, so retries and repeated syncs never duplicate an entry.

Run as its OWN process when embedding (the repo's process-isolation rule):
`embed_learned` loads chroma + torch. The JSON half imports nothing heavy.
"""

from __future__ import annotations

import os

# Same thread-pinning as pipeline/embed.py — must precede the native libs
# that embed_learned() lazily loads.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import json
from pathlib import Path

import config
from actions.models import Intervention

DATA_LEARNED = config.ROOT / "data" / "learned"


def learned_path(profile: str) -> Path:
    """Trusted, retrievable knowledge. Validated-effective interventions only."""
    return DATA_LEARNED / f"learned_resolutions_{profile}.json"


def pending_path(profile: str) -> Path:
    """The full audit trail of approvals. Never embedded."""
    return DATA_LEARNED / f"pending_resolutions_{profile}.json"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return []


def _save(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                    encoding="utf-8")


# ── Shaping ──────────────────────────────────────────────────────────────────

def build_resolution(profile: str, decision: dict, n_existing: int) -> dict:
    """Shape a Gate-2 decision into the corpus entry shape.

    Used for the PENDING store. The `outcome` field says plainly that this was
    only approved — nothing here claims it worked."""
    fix = decision.get("modified_fix") or decision.get("original_fix_snapshot") or {}
    case = decision.get("case_snapshot") or {}
    steps = "; ".join(fix.get("steps", []))
    edited = " (edited by the reviewer before approval)" \
        if decision.get("modified_fix") else ""
    return {
        "resolution_id": f"RES-PND-{profile[:3].upper()}-{n_existing + 1:03d}",
        "profile": profile,
        "bottleneck_type": case.get("type", "other"),
        "stage": case.get("stage", ""),
        "problem_description": case.get("description") or fix.get("rationale", ""),
        "action_taken": f"{fix.get('summary', '')}: {steps}",
        "outcome": (f"Approved at HITL Gate 2 on "
                    f"{decision.get('decided_at', '')[:10]}{edited}. "
                    f"Not yet measured — awaiting an outcome review."),
        "days_to_resolve": None,
        "source": "pending",
        "status": "approved_unmeasured",
        "decision_id": decision.get("decision_id"),
        "intervention_id": decision.get("intervention_id"),
    }


def build_resolution_from_intervention(profile: str, intervention: Intervention,
                                       n_existing: int) -> dict:
    """Shape a VALIDATED intervention into a trusted corpus entry.

    The `outcome` text carries the actual measurement — baseline, observed
    value and who confirmed it — because that evidence is what makes this
    entry worth retrieving later."""
    outcome = intervention.outcome
    steps = "; ".join(intervention.action_steps)
    case = intervention.case_snapshot or {}

    measured = ""
    if outcome:
        if (outcome.baseline_value is not None
                and outcome.observed_value is not None):
            measured = (f" {intervention.metric_label or 'Metric'} moved "
                        f"{outcome.baseline_value:g} → {outcome.observed_value:g}")
            if outcome.percent_change is not None:
                measured += f" ({outcome.percent_change:+.1f}%)"
            measured += "."
        # The reason often restates the same numbers; only add what it says
        # beyond them (a resolved finding, or a human overruling the reading).
        reason = outcome.effective_reason or ""
        if reason and reason.strip() not in measured:
            measured += f" {reason}"

    days = None
    if intervention.created_at and outcome and outcome.measured_at:
        try:
            from datetime import datetime
            start = datetime.fromisoformat(intervention.created_at)
            end = datetime.fromisoformat(outcome.measured_at)
            days = max(0, (end - start).days)
        except ValueError:
            days = None

    return {
        "resolution_id": f"RES-LRN-{profile[:3].upper()}-{n_existing + 1:03d}",
        "profile": profile,
        "bottleneck_type": case.get("type") or intervention.finding_key.split("::")[0],
        # Case-level findings can span the workflow rather than sit at one
        # stage; say so rather than leaving the field blank.
        "stage": case.get("stage") or "across the workflow",
        "problem_description": case.get("description") or intervention.title,
        "action_taken": f"{intervention.recommended_action}: {steps}",
        "outcome": (f"Validated as effective on "
                    f"{(outcome.validated_at or '')[:10] if outcome else ''}."
                    f"{measured}").strip(),
        "days_to_resolve": days,
        "source": "learned",
        "status": "validated_effective",
        "intervention_id": intervention.intervention_id,
        "decision_id": intervention.decision_id,
        "finding_key": intervention.finding_key,
    }


# ── Recording (untrusted) ────────────────────────────────────────────────────

def record_approval(profile: str, decision: dict,
                    path: Path | None = None) -> dict | None:
    """Log an approval to the PENDING store. Returns None if the decision was
    not an approval or has already been recorded.

    This is deliberately a dead end for retrieval: nothing written here can be
    diagnosed against until an outcome review promotes it."""
    if decision.get("action") not in ("approve", "modify"):
        return None
    path = path or pending_path(profile)
    entries = _load(path)
    if any(e.get("decision_id") == decision.get("decision_id") for e in entries):
        return None
    entry = build_resolution(profile, decision, len(entries))
    entries.append(entry)
    _save(path, entries)
    return entry


# ── Promotion (trusted) ──────────────────────────────────────────────────────

def promote_validated(profile: str, intervention: Intervention,
                      path: Path | None = None) -> dict | None:
    """Add a validated-effective intervention to the trusted store.

    Returns None when the intervention has not earned it, or is already there —
    so calling this repeatedly is safe and produces exactly one entry."""
    if not intervention.is_trusted_knowledge:
        return None
    path = path or learned_path(profile)
    entries = _load(path)
    if any(e.get("intervention_id") == intervention.intervention_id
           for e in entries):
        return None
    entry = build_resolution_from_intervention(profile, intervention, len(entries))
    entries.append(entry)
    _save(path, entries)
    return entry


def sync_from_interventions(profile: str, *, interventions=None,
                            path: Path | None = None) -> list[dict]:
    """Promote every intervention that has become trusted since the last sync."""
    from actions import store as action_store

    items = (interventions if interventions is not None
             else action_store.load_interventions(profile))
    added: list[dict] = []
    for intervention in items:
        entry = promote_validated(profile, intervention, path)
        if entry:
            added.append(entry)
    return added


# ── Migration ────────────────────────────────────────────────────────────────

def migrate_legacy(profile: str, *, learned: Path | None = None,
                   pending: Path | None = None) -> dict:
    """One-off: demote entries written under the old approval-is-proof rule.

    Anything in the trusted store without an `intervention_id` was promoted on
    approval alone. It is moved to the pending store — kept in full, so no
    history is lost — and stops being retrievable as advice."""
    learned = learned or learned_path(profile)
    pending = pending or pending_path(profile)
    trusted_entries = _load(learned)
    if not trusted_entries:
        return {"moved": 0, "kept": 0}

    keep = [e for e in trusted_entries if e.get("intervention_id")]
    move = [e for e in trusted_entries if not e.get("intervention_id")]
    if not move:
        return {"moved": 0, "kept": len(keep)}

    pending_entries = _load(pending)
    known = {e.get("decision_id") for e in pending_entries}
    for entry in move:
        if entry.get("decision_id") in known:
            continue
        pending_entries.append({
            **entry,
            "resolution_id": entry["resolution_id"].replace("RES-LRN-", "RES-PND-"),
            "source": "pending",
            "status": "approved_unmeasured",
            "outcome": (entry.get("outcome", "")
                        + " Demoted: approval alone is not evidence of effect."),
        })
    _save(pending, pending_entries)
    _save(learned, keep)
    return {"moved": len(move), "kept": len(keep)}


# ── Embedding (heavy — own process) ──────────────────────────────────────────

def embed_learned(profile: str, path: Path | None = None) -> int:
    """Upsert the TRUSTED learned resolutions into sme_resolutions.

    Reads only `learned_resolutions_<p>.json`. The pending store is never
    embedded — that is the whole safeguard."""
    from pipeline import embed
    from pipeline.embed_resolutions import get_resolutions_collection

    entries = _load(path or learned_path(profile))
    if not entries:
        return 0
    model = embed._get_model()
    texts = [f"{e['problem_description']} {e['action_taken']} {e['outcome']}"
             for e in entries]
    embeddings = model.encode(texts, show_progress_bar=False,
                              normalize_embeddings=True).tolist()
    # Chroma metadata forbids None (a learned entry may have no days_to_resolve).
    metadatas = [{k: e[k] for k in ("profile", "bottleneck_type", "stage",
                                    "days_to_resolve", "source")
                  if e.get(k) is not None}
                 for e in entries]
    get_resolutions_collection().upsert(
        ids=[e["resolution_id"] for e in entries], documents=texts,
        embeddings=embeddings, metadatas=metadatas)
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record Gate-2 approvals and promote validated interventions")
    parser.add_argument("--profile", required=True,
                        choices=sorted(config.MESSY_PROFILES))
    parser.add_argument("--decision-json", type=Path, default=None,
                        help="a Gate-2 decision record -> the pending store")
    parser.add_argument("--promote", action="store_true",
                        help="promote validated interventions and re-embed")
    parser.add_argument("--migrate-legacy", action="store_true",
                        help="demote pre-outcome-gate entries to the pending store")
    args = parser.parse_args()

    if args.migrate_legacy:
        stats = migrate_legacy(args.profile)
        print(f"[learn] migrated: {stats['moved']} approval-only entries demoted "
              f"to pending, {stats['kept']} validated entries kept")
        return

    if args.decision_json:
        decision = json.loads(args.decision_json.read_text(encoding="utf-8-sig"))
        entry = record_approval(args.profile, decision)
        if entry is None:
            print(f"[learn] nothing recorded (action={decision.get('action')!r}, "
                  f"or already logged)")
        else:
            print(f"[learn] {entry['resolution_id']} recorded as PENDING -> "
                  f"{pending_path(args.profile).relative_to(config.ROOT)}. "
                  f"It becomes retrievable knowledge only after an outcome "
                  f"review validates it.")

    if args.promote:
        added = sync_from_interventions(args.profile)
        n = embed_learned(args.profile)
        print(f"[learn] promoted {len(added)} validated intervention(s); "
              f"{n} trusted entries embedded into "
              f"'{config.RESOLUTIONS_COLLECTION}'")


if __name__ == "__main__":
    main()
