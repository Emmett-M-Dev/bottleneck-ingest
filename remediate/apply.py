"""Apply an approved remediation plan — write CLEANED COPIES, never the original.

For each file in the plan, rewrite its status column through the value map and
save to data/synthetic/messy_<profile>_cleaned/<same filename>. The original
drive is untouched, so the demo is repeatable and the HITL story is honest:
the human approved a preview, the executor produced a new artefact, and the old
one still exists to diff against. Returns a structured before->after diff.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config
from remediate.schemas import DiffRow, RemediationPlan, RemediationResult

_DIFF_SAMPLE_CAP = 40


def cleaned_dir(profile: str) -> Path:
    base = config.MESSY_PROFILES[profile]["dir"]
    return base.parent / f"{base.name}_cleaned"


def apply_plan(plan: RemediationPlan) -> RemediationResult:
    drive = config.MESSY_PROFILES[plan.profile]["dir"]
    out_dir = cleaned_dir(plan.profile)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    diff: list[DiffRow] = []
    changed = 0

    for f in plan.files:
        vmap = {v.original: v.canonical for v in f.value_map}
        src = drive / f.filename
        df = pd.read_excel(src, sheet_name=f.sheet, dtype=str)
        col = f.status_column
        if col in df.columns:
            for i, raw in enumerate(df[col].tolist()):
                key = "" if raw is None or pd.isna(raw) else str(raw)
                new = vmap.get(key, key)
                if new != key:
                    changed += 1
                    if len(diff) < _DIFF_SAMPLE_CAP:
                        diff.append(DiffRow(
                            filename=f.filename,
                            source_ref=f"{f.filename}:{f.sheet}:{i + 2}",
                            column=col, before=key, after=new))
                df.at[i, col] = new
        dest = out_dir / f.filename
        df.to_excel(dest, index=False, engine="openpyxl")
        written.append(str(dest))

    return RemediationResult(
        profile=plan.profile,
        applied_at=datetime.now(timezone.utc).isoformat(),
        output_dir=str(out_dir),
        files_written=written,
        cells_changed=changed,
        diff_sample=diff,
    )
