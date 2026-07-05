"""Assemble a MappingProposal for one messy drive.

Always computes the heuristic baseline (alias lookup via
`mapping.resolve_columns` + `config.COLUMN_MAP` — the pre-LLM state of the
art in this repo); in normal mode the LLM proposal is the headline and the
baseline rides along under `"baseline"` as the eval comparison condition.
`--offline` promotes the baseline to the proposal itself, keeping every demo
runnable with zero API calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config
from audit.scan import scan_drive
from audit.schemas import (ColumnMapping, FileProposal, LLMFileProposal,
                           LLMProposal, MappingProposal, MessReport)
from mapping import resolve_columns

# A sheet counts as event-shaped for the baseline iff at least this many
# canonical fields resolve from the header aliases.
_BASELINE_EVENTS_MIN_FIELDS = 3
_BASELINE_CONFIDENCE = 0.4


def baseline_proposal(scan: dict) -> LLMProposal:
    """What the pipeline could infer BEFORE the LLM existed: exact-alias
    header lookup. No role nuance (reference/notes indistinguishable from
    noise), no cross-file reasoning, empty mess report — that is the point."""
    files: list[LLMFileProposal] = []
    for sheet in scan["sheets"]:
        resolved = resolve_columns(sheet["headers"], config.COLUMN_MAP)
        header_to_field = {src: canon for canon, src in resolved.items()}
        is_events = len(resolved) >= _BASELINE_EVENTS_MIN_FIELDS
        files.append(LLMFileProposal(
            filename=sheet["filename"],
            sheet=sheet["sheet"],
            inferred_role="events" if is_events else "ignore",
            role_confidence=_BASELINE_CONFIDENCE,
            include=is_events,
            columns=[
                ColumnMapping(source_header=h,
                              canonical_field=header_to_field.get(h),
                              confidence=_BASELINE_CONFIDENCE)
                for h in sheet["headers"]
            ],
            issues=[],
        ))
    return LLMProposal(files=files,
                       mess_report=MessReport(duplicates=[], overlaps=[],
                                              gaps=[], irrelevant=[]))


def _attach_row_counts(files: list[LLMFileProposal], scan: dict) -> list[FileProposal]:
    counts = {(s["filename"], s["sheet"]): s["n_rows"] for s in scan["sheets"]}
    return [FileProposal(**f.model_dump(),
                         n_rows=counts.get((f.filename, f.sheet), 0))
            for f in files]


def build_proposal(drive: Path | str, profile: str, *, offline: bool = False,
                   model: str | None = None, client=None) -> MappingProposal:
    scan = scan_drive(drive)
    baseline = baseline_proposal(scan)

    if offline:
        headline, mode, used_model = baseline, "offline", "none"
    else:
        from audit.infer import DEFAULT_MODEL, propose_with_llm
        headline = propose_with_llm(scan, model=model, client=client)
        mode, used_model = "llm", model or DEFAULT_MODEL

    return MappingProposal(
        profile=profile,
        drive=str(drive),
        generated_at=datetime.now(timezone.utc).isoformat(),
        model=used_model,
        mode=mode,
        files=_attach_row_counts(headline.files, scan),
        mess_report=headline.mess_report,
        baseline=baseline,
    )


def write_proposal(proposal: MappingProposal) -> Path:
    out = config.ui_mapping_proposal_path(proposal.profile)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proposal.model_dump(), indent=2), encoding="utf-8")
    return out
