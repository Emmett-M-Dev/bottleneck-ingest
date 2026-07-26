"""Persistence for the action layer — plain JSON files, one set per profile.

    outputs/actions_<profile>.json         the action queue (ActionItem[])
    outputs/interventions_<profile>.json   tracked commitments (Intervention[])
    outputs/snapshots_<profile>.jsonl      one AnalysisSnapshot per analysis run

Files, not a database, for the same reason the rest of the system is
file-based: it stays inspectable, diffable and committable, and it needs no
service to run on a worker's laptop. The write is atomic (temp file + replace)
so a crashed run cannot leave a half-written queue behind.

Merging matters more than writing here. A re-analysis must refresh the
*evidence* on an item a worker has already picked up without resetting their
owner, due date or progress — see `merge_actions`.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import config
from actions.models import ActionItem, AnalysisSnapshot, Intervention

# Fields owned by the human, not the detector. A re-analysis never overwrites
# these; everything else (evidence, metrics, impact) is refreshed from the run.
_HUMAN_OWNED = ("owner", "due_date", "status", "intervention_id")


def actions_path(profile: str) -> Path:
    return config.OUTPUTS / f"actions_{profile}.json"


def interventions_path(profile: str) -> Path:
    return config.OUTPUTS / f"interventions_{profile}.json"


def snapshots_path(profile: str) -> Path:
    return config.OUTPUTS / f"snapshots_{profile}.jsonl"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _dump(models: list) -> str:
    return json.dumps([m.model_dump() for m in models],
                      ensure_ascii=False, indent=2)


# ── Action items ─────────────────────────────────────────────────────────────

def load_actions(profile: str, path: Path | None = None) -> list[ActionItem]:
    path = path or actions_path(profile)
    if not path.exists():
        return []
    return [ActionItem.model_validate(d)
            for d in json.loads(path.read_text(encoding="utf-8-sig"))]


def save_actions(profile: str, items: list[ActionItem],
                 path: Path | None = None) -> Path:
    path = path or actions_path(profile)
    _write_atomic(path, _dump(items))
    return path


def merge_actions(existing: list[ActionItem],
                  fresh: list[ActionItem]) -> list[ActionItem]:
    """Fold a new analysis into the standing queue.

    - An item the worker has already touched keeps its owner, due date, status
      and intervention link; its evidence, metrics and impact are refreshed.
    - A finding that has stopped appearing is KEPT (so the worker can see what
      they were working on), but flagged `finding_resolved` in its summary is
      NOT done here — that judgement belongs to actions/outcome.py, which has
      the snapshots to justify it.
    - Genuinely new findings are appended.
    """
    by_id = {item.action_id: item for item in existing}
    merged: list[ActionItem] = []
    seen: set[str] = set()

    for item in fresh:
        seen.add(item.action_id)
        prior = by_id.get(item.action_id)
        if prior is None:
            merged.append(item)
            continue
        refreshed = item.model_copy(deep=True)
        for field in _HUMAN_OWNED:
            setattr(refreshed, field, getattr(prior, field))
        refreshed.created_at = prior.created_at or item.created_at
        merged.append(refreshed)

    # Standing items whose finding did not recur this run.
    merged.extend(item for item in existing if item.action_id not in seen)
    return merged


# ── Interventions ────────────────────────────────────────────────────────────

def load_interventions(profile: str,
                       path: Path | None = None) -> list[Intervention]:
    path = path or interventions_path(profile)
    if not path.exists():
        return []
    return [Intervention.model_validate(d)
            for d in json.loads(path.read_text(encoding="utf-8-sig"))]


def save_interventions(profile: str, items: list[Intervention],
                       path: Path | None = None) -> Path:
    path = path or interventions_path(profile)
    _write_atomic(path, _dump(items))
    return path


def upsert_intervention(profile: str, intervention: Intervention,
                        path: Path | None = None) -> list[Intervention]:
    """Replace-by-id, then persist. Keeps the store idempotent under retries."""
    items = load_interventions(profile, path)
    items = [i for i in items if i.intervention_id != intervention.intervention_id]
    items.append(intervention)
    save_interventions(profile, items, path)
    return items


def find_intervention(profile: str, intervention_id: str,
                      path: Path | None = None) -> Intervention | None:
    for i in load_interventions(profile, path):
        if i.intervention_id == intervention_id:
            return i
    return None


def interventions_for_action(profile: str, action_id: str,
                             path: Path | None = None) -> list[Intervention]:
    return [i for i in load_interventions(profile, path)
            if i.action_id == action_id]


# ── Snapshots ────────────────────────────────────────────────────────────────

def append_snapshot(profile: str, snapshot: AnalysisSnapshot,
                    path: Path | None = None) -> Path:
    path = path or snapshots_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot.model_dump(), ensure_ascii=False) + "\n")
    return path


def load_snapshots(profile: str, path: Path | None = None) -> list[AnalysisSnapshot]:
    path = path or snapshots_path(profile)
    if not path.exists():
        return []
    out: list[AnalysisSnapshot] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(AnalysisSnapshot.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue  # a truncated line must not sink the whole history
    return out


def latest_snapshot(profile: str,
                    path: Path | None = None) -> AnalysisSnapshot | None:
    snaps = load_snapshots(profile, path)
    return snaps[-1] if snaps else None
