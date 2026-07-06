"""Propose a status value-map: freetext -> controlled vocabulary.

Offline mode is a deterministic keyword rule (done/paid/ok words -> Complete;
blank/"n/a" -> the obvious bucket; everything else -> Open) — instant, testable,
and the safe default. LLM mode asks Claude to map the distinct values, which
generalises to freetext an SME invents that no rule anticipates (the genuine
open-endedness). Same audit-agent shape: one schema-constrained call, no
sampling params, injectable client for tests.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import config
from audit.schemas import ApprovedMapping, load_approved
from remediate.scan import scan_statuses
from remediate.schemas import (CONTROLLED_VOCAB, FileRemediation, RemediationPlan,
                               ValueMapping)

_DONE_WORDS = {"done", "complete", "completed", "ok", "okay", "paid", "sorted",
               "finished", "received", "signed", "confirmed", "yes", "y"}
_NA_WORDS = {"n/a", "na", "not applicable", "-"}


def _rule_map(raw: str) -> tuple[str, float]:
    """Deterministic fallback. Strips trailing ticks/whitespace, matches a
    done/na keyword, else Open. Confidence marks the guessy cases lower so the
    human reviewer looks at them."""
    norm = raw.strip().lower().rstrip("✔✓ ").strip()
    if not norm:
        return "Open", 0.5          # blank -> assume not yet done
    if norm in _NA_WORDS:
        return "N/A", 0.9
    if any(w in norm.split() or w == norm for w in _DONE_WORDS):
        return "Complete", 0.9
    return "Open", 0.6              # freetext note ("waiting on family") -> Open


def _offline_file(sheet: dict) -> FileRemediation:
    vmap = [ValueMapping(original=raw, canonical=(c := _rule_map(raw))[0],
                         count=n, confidence=c[1])
            for raw, n in sheet["values"].items()]
    return FileRemediation(filename=sheet["filename"], sheet=sheet["sheet"],
                           status_column=sheet["status_column"], value_map=vmap)


def _llm_files(scan: list[dict], model: str, client) -> list[FileRemediation]:
    import anthropic
    from pydantic import BaseModel

    class _Item(BaseModel):
        original: str
        canonical: str
        confidence: float

    class _LLMValueMap(BaseModel):
        items: list[_Item]

    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — run with --offline for "
                           "the rule-based value map.")
    client = client or anthropic.Anthropic()
    system = (
        "You normalise a spreadsheet's freetext status column to a closed "
        f"controlled vocabulary: {CONTROLLED_VOCAB}. 'Complete' = the task is "
        "done/paid/received; 'Open' = still outstanding or a freetext note about "
        "waiting/chasing; 'N/A' = explicitly not applicable. Map every distinct "
        "value; lower the confidence on genuinely ambiguous ones."
    )
    files: list[FileRemediation] = []
    for sheet in scan:
        distinct = [{"value": v, "count": n} for v, n in sheet["values"].items()]
        resp = client.messages.parse(
            model=model, max_tokens=4000, system=system,
            messages=[{"role": "user", "content": json.dumps(distinct)}],
            output_format=_LLMValueMap,
        )
        by_orig = {it.original: it for it in resp.parsed_output.items}
        vmap = []
        for raw, n in sheet["values"].items():
            it = by_orig.get(raw)
            canon = it.canonical if it and it.canonical in CONTROLLED_VOCAB else "Open"
            conf = it.confidence if it else 0.5
            vmap.append(ValueMapping(original=raw, canonical=canon, count=n, confidence=conf))
        files.append(FileRemediation(filename=sheet["filename"], sheet=sheet["sheet"],
                                     status_column=sheet["status_column"], value_map=vmap))
    return files


def build_plan(profile: str, *, offline: bool = True, mapping_path=None,
               model: str | None = None, client=None) -> RemediationPlan:
    mapping_path = mapping_path or config.approved_mapping_path(profile)
    approved: ApprovedMapping = load_approved(mapping_path)
    drive = config.MESSY_PROFILES[profile]["dir"]
    scan = scan_statuses(drive, approved)

    if offline:
        files, mode, used = [_offline_file(s) for s in scan], "offline", "none"
    else:
        used = model or os.environ.get("AUDIT_MODEL", config.AUDIT_MODEL_DEFAULT)
        files, mode = _llm_files(scan, used, client), "llm"

    return RemediationPlan(profile=profile,
                           generated_at=datetime.now(timezone.utc).isoformat(),
                           model=used, mode=mode, files=files)


def remediation_plan_path(profile: str) -> Path:
    return config.OUTPUTS / f"ui_remediation_{profile}.json"


def write_plan(plan: RemediationPlan) -> Path:
    out = remediation_plan_path(plan.profile)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan.model_dump(), indent=2), encoding="utf-8")
    return out
