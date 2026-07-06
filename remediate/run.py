"""Remediation executor CLI.

    python -m remediate.run --profile foyle                 # propose (offline rules)
    python -m remediate.run --profile foyle --llm           # propose via Claude
    python -m remediate.run --profile foyle --apply         # propose + write cleaned copies

Propose writes outputs/ui_remediation_<profile>.json (served by the dashboard's
Fixes tab). Apply writes cleaned copies to messy_<profile>_cleaned/ and prints
the diff. Own process, no chroma/pyarrow/torch imports (Windows segfault rule).
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import json

import config
from remediate.apply import apply_plan
from remediate.propose import build_plan, write_plan


def remediation_result_path(profile: str):
    return config.OUTPUTS / f"ui_remediation_result_{profile}.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Propose/apply status-normalisation remediation")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    parser.add_argument("--llm", action="store_true", help="propose via Claude instead of rules")
    parser.add_argument("--apply", action="store_true", help="write cleaned copies after proposing")
    args = parser.parse_args()

    plan = build_plan(args.profile, offline=not args.llm)
    out = write_plan(plan)
    print(f"[remediate] {plan.mode} plan: {plan.total_cells_changed} status cells "
          f"across {len(plan.files)} files -> {out}")
    for f in plan.files:
        for v in f.value_map:
            if v.original.strip() != v.canonical:
                print(f"[remediate]   {f.filename}: {v.original!r} -> {v.canonical} (x{v.count})")

    if args.apply:
        result = apply_plan(plan)
        res_path = remediation_result_path(args.profile)
        res_path.write_text(json.dumps(result.model_dump(), indent=2), encoding="utf-8")
        print(f"\n[remediate] applied: {result.cells_changed} cells changed, "
              f"{len(result.files_written)} cleaned copies -> {result.output_dir}")
    else:
        print("[remediate] Next: approve it in the dashboard's Fixes tab, or re-run with --apply.")


if __name__ == "__main__":
    main()
