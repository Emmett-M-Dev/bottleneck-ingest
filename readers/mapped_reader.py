"""The generic, mapping-driven connector — no per-SME reader code.

Reads a messy drive folder through a human-APPROVED mapping
(mappings/approved_<profile>.json, written by the dashboard's Mapping Review
gate) and emits the canonical shape every connector emits:

    events role  -> event rows {case_id, activity, timestamp, actor, status, source_ref}
    reference/notes roles -> doc rows {source_ref, text} (RAG corpus only)
    ignore / include=false -> skipped

Cross-file dedup neutralises the classic mess pattern of a stale copy
overlapping its successor: after canonicalisation, rows with the same
(case_id, activity, timestamp) as an earlier file's row are dropped
(file order = approved order, keep first).

Stale-mapping guard: if a mapped file or header has vanished since approval,
fail loudly with re-run guidance rather than silently ingesting less.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from audit.schemas import ApprovedFileMapping, ApprovedMapping

logger = logging.getLogger(__name__)

_RERUN_HINT = ("The drive has changed since the mapping was approved — re-run "
               "`python -m audit.run` and re-approve it in the dashboard's "
               "Mapping Review tab.")


def _to_iso(value) -> str:
    """Mirror pipeline.normalise._to_iso semantics: parse day-first, keep the
    original string when unparseable rather than dropping the row."""
    ts = pd.to_datetime(value, dayfirst=True, errors="coerce", format="mixed")
    return str(value) if pd.isna(ts) else ts.isoformat()


def _read_file(drive: Path, file: ApprovedFileMapping) -> pd.DataFrame:
    path = drive / file.filename
    if not path.exists():
        raise FileNotFoundError(f"Mapped file missing: {path}. {_RERUN_HINT}")
    df = pd.read_excel(path, sheet_name=file.sheet, dtype=str)
    missing = [h for h in file.columns if h not in df.columns]
    if missing:
        raise ValueError(f"{file.filename}: approved headers {missing} no longer "
                         f"present (has {list(df.columns)}). {_RERUN_HINT}")
    return df


def _event_rows(drive: Path, file: ApprovedFileMapping) -> list[dict]:
    df = _read_file(drive, file)
    field_to_header = {f: h for h, f in file.columns.items() if f}
    rows: list[dict] = []
    for i, raw in enumerate(df.to_dict(orient="records")):
        def get(field: str):
            header = field_to_header.get(field)
            value = raw.get(header) if header else None
            return None if value is None or pd.isna(value) else str(value)

        case_id, activity, timestamp = get("case_id"), get("activity"), get("timestamp")
        source_ref = f"{file.filename}:{file.sheet}:{i + 2}"  # +2: header + 1-based
        if not (case_id and activity and timestamp):
            logger.warning("Incomplete row %s — no event extracted", source_ref)
            continue
        rows.append({"case_id": case_id, "activity": activity.strip(),
                     "timestamp": _to_iso(timestamp), "actor": get("actor"),
                     "status": get("status"), "source_ref": source_ref})
    return rows


def _doc_rows(drive: Path, file: ApprovedFileMapping) -> list[dict]:
    """Reference/notes sheets become one text snippet per row for the RAG
    corpus. Fully scrubbed downstream by normalise_text — raw values are fine
    here."""
    df = _read_file(drive, file)
    docs: list[dict] = []
    for i, raw in enumerate(df.to_dict(orient="records")):
        parts = [f"{k}: {v}" for k, v in raw.items()
                 if v is not None and not pd.isna(v) and str(v).strip()]
        if not parts:
            continue
        docs.append({"source_ref": f"{file.filename}:{file.sheet}:{i + 2}",
                     "text": f"{file.filename} — " + "; ".join(parts)})
    return docs


def _dedup_key(row: dict, keys: list[str]) -> tuple:
    def canon(field: str) -> str:
        value = row.get(field) or ""
        return value.strip().lower() if field == "activity" else value.strip()
    return tuple(canon(k) for k in keys)


def read_mapped(drive: Path | str, approved: ApprovedMapping) -> tuple[list[dict], list[dict]]:
    drive = Path(drive)
    event_rows: list[dict] = []
    doc_rows: list[dict] = []
    seen: set[tuple] = set()
    n_dropped = 0

    for file in approved.files:
        if not file.include or file.role == "ignore":
            logger.info("Skipping %s (role=%s include=%s)",
                        file.filename, file.role, file.include)
            continue
        if file.role == "events":
            for row in _event_rows(drive, file):
                key = _dedup_key(row, approved.dedup.keys)
                if key in seen:
                    n_dropped += 1
                    continue
                seen.add(key)
                event_rows.append(row)
        else:  # reference | notes
            doc_rows.extend(_doc_rows(drive, file))

    if n_dropped:
        logger.info("Dedup dropped %d duplicate event rows (keys=%s)",
                    n_dropped, approved.dedup.keys)
    return event_rows, doc_rows
