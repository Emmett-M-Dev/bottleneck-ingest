"""Learning loop: an approved Gate-2 fix becomes a retrievable past resolution.

    python -m pipeline.learn --profile foyle --decision-json <path>

When a human approves (or approves-with-edits) a suggested fix in the
dashboard, the decision record is fed back here: it is appended to
data/learned/learned_resolutions_<profile>.json and upserted into the SAME
`sme_resolutions` Chroma collection the diagnosis agent retrieves from —
tagged source="learned". The next diagnosis of a similar bottleneck retrieves
the organisation's own approved fix alongside the seeded corpus.

This turns HITL Gate 2 from a pure safety valve into a knowledge-curation
mechanism: only human-approved fixes enter the knowledge base, so the corpus
grows with exactly the fixes the SME itself signed off.

Idempotent on decision_id — replaying the same decision (API retries,
resumed agent runs) neither duplicates the file entry nor the embedding
(Chroma upsert by resolution_id).

Run as its OWN process (the API shells out): this module loads chroma +
torch via pipeline.embed_resolutions, so the repo's process-isolation rules
apply. The pure-JSON half (build/append) imports nothing heavy — tests use it
directly.
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

DATA_LEARNED = config.ROOT / "data" / "learned"


def learned_path(profile: str) -> Path:
    return DATA_LEARNED / f"learned_resolutions_{profile}.json"


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def build_resolution(profile: str, decision: dict, n_existing: int) -> dict:
    """Shape a Gate-2 decision record into the resolution-corpus entry shape
    (same keys as data/synthetic/resolutions_<profile>.json)."""
    fix = decision.get("modified_fix") or decision.get("original_fix_snapshot") or {}
    case = decision.get("case_snapshot") or {}
    steps = "; ".join(fix.get("steps", []))
    edited = " (edited by the reviewer before approval)" \
        if decision.get("modified_fix") else ""
    return {
        "resolution_id": f"RES-LRN-{profile[:3].upper()}-{n_existing + 1:03d}",
        "profile": profile,
        "bottleneck_type": case.get("type", "other"),
        "stage": case.get("stage", ""),
        "problem_description": case.get("description")
                               or fix.get("rationale", ""),
        "action_taken": f"{fix.get('summary', '')}: {steps}",
        "outcome": (f"Approved at HITL Gate 2 on "
                    f"{decision.get('decided_at', '')[:10]}{edited}."),
        "days_to_resolve": None,
        "source": "learned",
        "decision_id": decision.get("decision_id"),
    }


def append_learned(profile: str, decision: dict,
                   path: Path | None = None) -> dict | None:
    """Append the decision as a learned resolution; None if already recorded
    (dedup on decision_id) or the action wasn't an approval."""
    if decision.get("action") not in ("approve", "modify"):
        return None
    path = path or learned_path(profile)
    entries = _load(path)
    if any(e.get("decision_id") == decision.get("decision_id") for e in entries):
        return None
    entry = build_resolution(profile, decision, len(entries))
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return entry


def embed_learned(profile: str, path: Path | None = None) -> int:
    """Upsert every learned resolution into sme_resolutions. Heavy imports
    live here — call only from a dedicated process."""
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
    # Chroma metadata forbids None (learned entries have no days_to_resolve).
    metadatas = [{k: e[k] for k in ("profile", "bottleneck_type", "stage",
                                    "days_to_resolve", "source")
                  if e[k] is not None}
                 for e in entries]
    get_resolutions_collection().upsert(
        ids=[e["resolution_id"] for e in entries], documents=texts,
        embeddings=embeddings, metadatas=metadatas)
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Feed an approved Gate-2 decision into the resolution store")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    parser.add_argument("--decision-json", required=True, type=Path,
                        help="path to the decision record (the dashboard's POST body)")
    args = parser.parse_args()

    decision = json.loads(args.decision_json.read_text(encoding="utf-8-sig"))
    entry = append_learned(args.profile, decision)
    if entry is None:
        print(f"[learn] nothing to learn (action={decision.get('action')!r}, "
              f"or decision already recorded)")
        return
    n = embed_learned(args.profile)
    print(f"[learn] {entry['resolution_id']} added -> "
          f"{learned_path(args.profile).relative_to(config.ROOT)} "
          f"({n} learned entries embedded into '{config.RESOLUTIONS_COLLECTION}')")


if __name__ == "__main__":
    main()
