"""Schemas for the remediation executor — the third agent action.

Gate #1 (AI Fixes) records a decision; this closes the loop by letting the AI
actually AMEND the drive files. The concrete, safe, reversible edit grounded in
the audit's mess report is **status normalisation**: an SME's freetext status
column ("done ", "DONE", "ok", "Completed ✔", "complete" ...) collapses to a
small controlled vocabulary. The AI proposes the value map, a human approves it
(same HITL pattern as the mapping gate), and the executor writes CLEANED COPIES
— the originals are never touched.

Controlled vocabulary is closed; the executor only ever rewrites a status cell
to one of these, so an apply can never invent a new value.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

CanonicalStatus = Literal["Complete", "Open", "N/A"]
CONTROLLED_VOCAB: list[str] = ["Complete", "Open", "N/A"]


class ValueMapping(BaseModel):
    original: str          # the raw freetext value as it appears in the sheet
    canonical: CanonicalStatus
    count: int             # how many cells hold this value (impact preview)
    confidence: float


class FileRemediation(BaseModel):
    filename: str
    sheet: str
    status_column: str
    value_map: list[ValueMapping]

    @property
    def cells_changed(self) -> int:
        return sum(v.count for v in self.value_map if v.original.strip() != v.canonical)


class RemediationPlan(BaseModel):
    """outputs/ui_remediation_<profile>.json — proposed by remediate.propose,
    approved + applied via the dashboard's Fixes tab."""

    profile: str
    generated_at: str
    model: str
    mode: Literal["llm", "offline"]
    files: list[FileRemediation]

    @property
    def total_cells_changed(self) -> int:
        return sum(f.cells_changed for f in self.files)


class DiffRow(BaseModel):
    filename: str
    source_ref: str        # filename:sheet:excel_row
    column: str
    before: str
    after: str


class RemediationResult(BaseModel):
    """What an apply produced: where the cleaned copies went + a sample diff."""

    profile: str
    applied_at: str
    output_dir: str
    files_written: list[str]
    cells_changed: int
    diff_sample: list[DiffRow]


def load_plan(path: Path | str) -> RemediationPlan:
    return RemediationPlan.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
