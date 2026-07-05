"""Score mapping quality against ground truth — the dissertation numbers.

    python -m eval.score_mapping                    # every profile
    python -m eval.score_mapping --profile foyle

Three conditions per profile, scored against ground_truth_mapping_<profile>.json:

    baseline   the pre-LLM heuristic (alias lookup), from the proposal's
               "baseline" block
    llm        the audit agent's proposal (outputs/ui_mapping_proposal_*.json)
    approved   the human-approved mapping (mappings/approved_*.json) — the
               gate-#2 output

Metrics: file-role accuracy, include accuracy, per-column exact accuracy
(nulls count), and precision/recall/F1 over non-null column assignments
(prediction = (file, header) -> field). The HITL delta — what the human
correction bought — is `approved` minus `llm`, alongside the correction
counts and time-to-approve read from the dashboard's decision log.

Writes outputs/eval_mapping_<profile>.json per profile.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import config

_DECISION_LOG = config.ROOT.parent / "hitl-interface" / "logs" / "mapping_decisions.jsonl"


# ── condition -> {(filename, sheet): {"role", "include", "columns"}} ─────────

def _from_gt(gt: dict) -> dict:
    return {(f["filename"], f["sheet"]): {
        "role": f["role"], "include": f["include"], "columns": f["columns"]}
        for f in gt["files"]}


def _from_proposal_files(files: list[dict]) -> dict:
    return {(f["filename"], f["sheet"]): {
        "role": f["inferred_role"], "include": f["include"],
        "columns": {c["source_header"]: c["canonical_field"] for c in f["columns"]}}
        for f in files}


def _from_approved(approved: dict) -> dict:
    return {(f["filename"], f["sheet"]): {
        "role": f["role"], "include": f["include"], "columns": f["columns"]}
        for f in approved["files"]}


# ── scoring ──────────────────────────────────────────────────────────────────

def score_condition(truth: dict, pred: dict) -> dict:
    """Compare one condition against ground truth. The header universe per
    file comes from the prediction (= the scanned headers); a GT file with an
    empty columns dict expects every header unmapped (None)."""
    n_files = len(truth)
    role_ok = include_ok = 0
    cols_total = cols_exact = 0
    tp = fp = fn = 0

    for key, t in truth.items():
        p = pred.get(key)
        if p is None:
            fn += sum(1 for v in t["columns"].values() if v)
            continue
        role_ok += p["role"] == t["role"]
        include_ok += p["include"] == t["include"]
        for header, predicted in p["columns"].items():
            expected = t["columns"].get(header)
            cols_total += 1
            cols_exact += predicted == expected
            if predicted and expected:
                if predicted == expected:
                    tp += 1
                else:
                    fp += 1
                    fn += 1
            elif predicted and not expected:
                fp += 1
            elif expected and not predicted:
                fn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "role_accuracy": round(role_ok / n_files, 3),
        "include_accuracy": round(include_ok / n_files, 3),
        "column_exact_accuracy": round(cols_exact / cols_total, 3) if cols_total else 0.0,
        "column_precision": round(precision, 3),
        "column_recall": round(recall, 3),
        "column_f1": round(f1, 3),
        "column_errors": cols_total - cols_exact,
    }


def _latest_decision(profile: str) -> dict | None:
    if not _DECISION_LOG.exists():
        return None
    latest = None
    for line in _DECISION_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("profile") == profile:
            latest = d
    return latest


def evaluate(profile: str) -> dict:
    cfg = config.MESSY_PROFILES[profile]
    truth = _from_gt(json.loads(cfg["gt_mapping"].read_text(encoding="utf-8")))

    proposal_path = config.ui_mapping_proposal_path(profile)
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    approved_path = config.approved_mapping_path(profile)

    conditions: dict[str, dict] = {}
    if proposal.get("baseline"):
        conditions["baseline"] = score_condition(
            truth, _from_proposal_files(proposal["baseline"]["files"]))
    conditions[proposal["mode"]] = score_condition(
        truth, _from_proposal_files(proposal["files"]))
    if approved_path.exists():
        approved = json.loads(approved_path.read_text(encoding="utf-8"))
        conditions["approved"] = score_condition(truth, _from_approved(approved))

    result = {"profile": profile, "model": proposal.get("model"),
              "proposal_generated_at": proposal.get("generated_at"),
              "conditions": conditions}

    decision = _latest_decision(profile)
    if decision:
        result["hitl"] = {
            "action": decision["action"],
            "corrections": decision["corrections"],
            "time_to_decision_seconds": decision["time_to_decision_seconds"],
            "decided_at": decision["decided_at"],
        }
    if "llm" in conditions and "approved" in conditions:
        result["hitl_error_delta"] = (conditions["llm"]["column_errors"]
                                      - conditions["approved"]["column_errors"])
    return result


def _print_table(result: dict) -> None:
    metrics = ["role_accuracy", "include_accuracy", "column_exact_accuracy",
               "column_precision", "column_recall", "column_f1", "column_errors"]
    conds = list(result["conditions"])
    print(f"\n=== {result['profile']} (model: {result['model']}) ===")
    print(f"{'metric':24s}" + "".join(f"{c:>12s}" for c in conds))
    for m in metrics:
        row = "".join(f"{result['conditions'][c][m]:>12}" for c in conds)
        print(f"{m:24s}{row}")
    if "hitl" in result:
        h = result["hitl"]
        c = h["corrections"]
        total = c["role_changes"] + c["column_changes"] + c["include_toggles"]
        print(f"gate #2: {h['action']} with {total} corrections "
              f"({c['role_changes']} roles, {c['column_changes']} columns, "
              f"{c['include_toggles']} include) in {h['time_to_decision_seconds']}s")
    if "hitl_error_delta" in result:
        print(f"HITL delta: human review removed {result['hitl_error_delta']} "
              f"column errors from the LLM proposal")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score mapping proposals against ground truth")
    parser.add_argument("--profile", choices=sorted(config.MESSY_PROFILES), default=None,
                        help="one profile (default: all)")
    args = parser.parse_args()

    profiles = [args.profile] if args.profile else sorted(config.MESSY_PROFILES)
    for profile in profiles:
        result = evaluate(profile)
        _print_table(result)
        out = config.OUTPUTS / f"eval_mapping_{profile}.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"-> {out.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
