"""Score bottleneck detection against the seeded ground truth.

    python -m eval.score_detection --profile foyle

Two conditions per profile, mirroring the mapping eval's baseline->LLM
gradient:

    baseline — detect_generic, the original marker detector: it must be TOLD
               which stages matter (config markers) and reads repetition /
               rework as stage PRESENCE
    dynamic  — detect_dynamic, the statistical detector: no markers; outlier
               gaps, duplicate entries and backward transitions found from
               the data alone

Per pattern type: precision / recall / F1 over affected-case sets. The events
are rebuilt through the GROUND-TRUTH mapping + the real mapped reader (dedup
included), so the score isolates detection quality from mapping quality and
does not depend on which profile happens to be ingested right now.

If a live export exists (outputs/ui_cases_<profile>.json with per-case
confidence + type), a calibration table is appended: the LLM's stated
confidence next to whether its retrieved evidence actually matched the
bottleneck type — the honest footnote for the "is 0.6 meaningful?" question.

Writes outputs/eval_detection_<profile>.json.
"""

from __future__ import annotations

import argparse
import json

import pandas as pd

import config
from detection.detect import detect_generic
from detection.dynamic import detect_dynamic

TYPES = ("delay", "repetition", "rework")


def _events_df(profile: str) -> pd.DataFrame:
    """Events via the ground-truth mapping + real mapped reader (incl. dedup)."""
    from audit.schemas import ApprovedFileMapping, ApprovedMapping
    from readers.mapped_reader import read_mapped

    gt_map = json.loads(config.MESSY_PROFILES[profile]["gt_mapping"]
                        .read_text(encoding="utf-8"))
    approved = ApprovedMapping(
        profile=profile, approved_at="2026-01-01T00:00:00+00:00",
        source_proposal_generated_at="2026-01-01T00:00:00+00:00",
        files=[ApprovedFileMapping(
            filename=f["filename"], sheet=f["sheet"], role=f["role"],
            include=f["include"], columns=f["columns"]) for f in gt_map["files"]],
    )
    event_rows, _ = read_mapped(config.MESSY_PROFILES[profile]["dir"], approved)
    df = pd.DataFrame(event_rows)
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _by_type(bottlenecks) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {t: set() for t in TYPES}
    for bn in bottlenecks:
        if bn.type in out:
            out[bn.type] |= set(bn.affected_cases)
    return out


def _prf(detected: set[str], truth: set[str]) -> dict:
    tp, fp, fn = len(detected & truth), len(detected - truth), len(truth - detected)
    p = tp / (tp + fp) if tp + fp else (1.0 if not truth else 0.0)
    r = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}


def _calibration_rows(profile: str) -> list[dict]:
    """LLM confidence vs retrieved-evidence type match, from the last live
    export. Empty when no export or the export predates per-case types."""
    cases_path = config.OUTPUTS / f"ui_cases_{profile}.json"
    res_path = config.resolutions_path(profile)
    if not cases_path.exists() or not res_path.exists():
        return []
    id_to_type = {r["resolution_id"]: r["bottleneck_type"]
                  for r in json.loads(res_path.read_text(encoding="utf-8"))}
    rows = []
    for case in json.loads(cases_path.read_text(encoding="utf-8")):
        bn_type = case.get("type")
        if bn_type not in TYPES:
            continue
        retrieved = [r.get("resolution_id") for r in case.get("retrieved_resolutions", [])]
        types = [id_to_type.get(rid) for rid in retrieved if rid in id_to_type]
        rows.append({
            "case_id": case["case_id"], "type": bn_type,
            "confidence": case.get("confidence"),
            "retrieved_type_match_rate":
                round(sum(t == bn_type for t in types) / len(types), 2) if types else None,
        })
    return rows


def score(profile: str) -> dict:
    truth = {t: set(v) for t, v in
             json.loads(config.MESSY_PROFILES[profile]["gt_bottlenecks"]
                        .read_text(encoding="utf-8"))["bottlenecks"].items()}
    df = _events_df(profile)

    markers = config.MESSY_PROFILES[profile]["markers"]
    stage_order = config.MESSY_PROFILES[profile]["stage_order"]
    conditions = {
        "baseline_markers": _by_type(detect_generic(df, **markers)),
        "dynamic": _by_type(detect_dynamic(df, stage_order)),
    }

    result = {"profile": profile, "cases": int(df["case_id"].nunique()),
              "conditions": {}}
    for name, detected in conditions.items():
        per_type = {t: _prf(detected[t], truth[t]) for t in TYPES}
        macro_f1 = round(sum(m["f1"] for m in per_type.values()) / len(TYPES), 3)
        result["conditions"][name] = {"per_type": per_type, "macro_f1": macro_f1}

    calibration = _calibration_rows(profile)
    if calibration:
        result["diagnosis_calibration"] = calibration
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Score detection vs seeded ground truth")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    args = parser.parse_args()

    result = score(args.profile)
    out = config.OUTPUTS / f"eval_detection_{args.profile}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Detection eval — {args.profile} ({result['cases']} cases)")
    for name, cond in result["conditions"].items():
        parts = ", ".join(f"{t}: P={m['precision']} R={m['recall']}"
                          for t, m in cond["per_type"].items())
        print(f"  {name:18s} macro-F1 {cond['macro_f1']:.3f}  ({parts})")
    print(f"Wrote {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
