"""Pydantic schemas for the mapping-agent seam.

Three artefacts share these shapes:

    outputs/ui_mapping_proposal_<profile>.json   (pipeline -> dashboard)
    mappings/approved_<profile>.json             (dashboard -> pipeline)
    the LLM's structured output                  (Claude -> audit/infer.py)

Pydantic is deliberately confined to this package: the anthropic SDK's
`messages.parse` needs it for schema-constrained output, and it arrives as an
SDK dependency anyway. The core pipeline's `models.py` stays plain dataclasses.

Structured-output constraints honoured here: closed vocabularies via Literal,
no recursive schemas, no numeric min/max bounds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

# The canonical Event fields a source column can map to. Closed vocabulary —
# the whole point of the canonical layer is that this list never grows per SME.
# "value" is the one addition since the original five: an optional monetary
# size for the case, which lets the action layer rank findings by revenue at
# risk without any SME-specific code. It is deliberately absent from
# config.COLUMN_MAP, so the heuristic baseline condition in the mapping eval is
# unchanged by its introduction.
CanonicalField = Literal["case_id", "activity", "timestamp", "actor", "status",
                         "value"]

# What a file is for. "events" feeds the event log; "reference" is context
# (RAG corpus only); "notes" is freetext (RAG corpus only); "ignore" is noise.
Role = Literal["events", "reference", "notes", "ignore"]


class ColumnMapping(BaseModel):
    source_header: str
    canonical_field: Optional[CanonicalField]
    confidence: float


class MessReport(BaseModel):
    duplicates: list[str]
    overlaps: list[str]
    gaps: list[str]
    irrelevant: list[str]


class LLMFileProposal(BaseModel):
    """Per-sheet judgement the LLM returns. n_rows etc. are joined back on
    from the scan afterwards — the model never echoes counts it could garble."""

    filename: str
    sheet: str
    inferred_role: Role
    role_confidence: float
    include: bool
    columns: list[ColumnMapping]
    issues: list[str]


class LLMProposal(BaseModel):
    """The complete structured output of one audit call."""

    files: list[LLMFileProposal]
    mess_report: MessReport


class FileProposal(LLMFileProposal):
    n_rows: int = 0


class MappingProposal(BaseModel):
    """The artefact written to outputs/ui_mapping_proposal_<profile>.json.
    `baseline` holds the heuristic condition (same files shape) for the eval."""

    profile: str
    drive: str
    generated_at: str
    model: str
    mode: Literal["llm", "offline"]
    files: list[FileProposal]
    mess_report: MessReport
    baseline: Optional[LLMProposal] = None


class DedupSpec(BaseModel):
    keys: list[str] = ["case_id", "activity", "timestamp"]
    keep: Literal["first"] = "first"


class ApprovedFileMapping(BaseModel):
    filename: str
    sheet: str
    role: Role
    include: bool
    columns: dict[str, Optional[CanonicalField]]  # source header -> field


class ApprovedMapping(BaseModel):
    """mappings/approved_<profile>.json — the human-confirmed mapping the
    mapped reader consumes. Written by the dashboard's Mapping Review gate."""

    profile: str
    approved_at: str
    source_proposal_generated_at: str
    files: list[ApprovedFileMapping]
    dedup: DedupSpec = DedupSpec()


def load_approved(path: Path | str) -> ApprovedMapping:
    return ApprovedMapping.model_validate(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
