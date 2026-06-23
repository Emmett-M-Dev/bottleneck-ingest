"""Raw reader output -> NormalisedRecord + Event rows.

Accepts raw dicts from any reader (excel / sheets share one shape; text is separate)
and produces the canonical contract. Column mapping uses the shared `mapping` helper,
so the alias logic lives in one place.

Scrubbing policy: PII in structured data lives in the `actor` field (staff names), so
that is scrubbed. The structural fields (case_id, activity, timestamp, status) are
controlled-vocabulary references carrying no PII — they are NOT scrubbed, because
running NER over them would corrupt the activity labels the detector depends on
(e.g. "Booking Confirmation" must survive verbatim). Free-text records (ops notes)
are fully scrubbed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from mapping import map_row
from models import Event, NormalisedRecord
from scrub.anonymise import scrub_actor, scrub_text

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(value) -> str | None:
    if value in (None, ""):
        return None
    ts = pd.to_datetime(value, dayfirst=True, errors="coerce")
    if pd.isna(ts):
        return str(value)  # unparseable: keep original rather than drop
    return ts.isoformat()


def _record_text(case_id, activity, timestamp, actor, status) -> str:
    parts = []
    if activity:
        parts.append(f"{activity}")
    if case_id:
        parts.append(f"for case {case_id}")
    if timestamp:
        parts.append(f"on {timestamp}")
    if actor:
        parts.append(f"handled by {actor}")
    if status:
        parts.append(f"(status: {status})")
    return " ".join(parts)


def normalise_structured(raw_rows: list[dict], source_type: str) -> list[NormalisedRecord]:
    """One NormalisedRecord per row; one Event where case_id+activity+timestamp present."""
    records: list[NormalisedRecord] = []
    ingested_at = _now_iso()

    for raw in raw_rows:
        source_ref = raw.get("_source_ref", "unknown")
        canon = map_row(raw)

        case_id = canon.get("case_id")
        activity = canon.get("activity")
        timestamp = _to_iso(canon.get("timestamp"))
        status = canon.get("status")

        actor_raw = canon.get("actor")
        actor, actor_reps = scrub_actor(actor_raw)

        structured = {
            "case_id": case_id,
            "activity": activity,
            "timestamp": timestamp,
            "actor": actor,
            "status": status,
        }
        text = _record_text(case_id, activity, timestamp, actor, status)

        events: list[Event] = []
        if case_id and activity and timestamp:
            events.append(Event(
                case_id=str(case_id),
                activity=str(activity),
                timestamp=str(timestamp),
                actor=actor,
                status=status,
                source_ref=source_ref,
            ))
        else:
            missing = [k for k, v in (("case_id", case_id), ("activity", activity),
                                       ("timestamp", timestamp)) if not v]
            logger.warning("Incomplete row %s: missing %s — no event extracted",
                           source_ref, ", ".join(missing))

        records.append(NormalisedRecord(
            record_id=source_ref,
            source_type=source_type,
            source_ref=source_ref,
            ingested_at=ingested_at,
            text=text,
            structured=structured,
            events=events,
            scrubbed_entities=actor_reps,
        ))
    return records


def normalise_foyle_events(event_rows: list[dict], source_type: str = "foyle") -> list[NormalisedRecord]:
    """Build one NormalisedRecord + Event per already-canonical Foyle event dict.

    The Foyle reader resolves columns itself (cross-sheet name canonicalisation), so
    these rows are canonical already — no COLUMN_MAP. Only the actor (host/mentor/
    staff name) is PII and gets scrubbed; the stage label is controlled vocabulary
    the detector depends on and is left verbatim.
    """
    records: list[NormalisedRecord] = []
    ingested_at = _now_iso()
    seq: dict[str, int] = {}

    for raw in event_rows:
        case_id = raw["case_id"]
        activity = raw["activity"]
        timestamp = raw["timestamp"]
        status = raw.get("status")
        actor, actor_reps = scrub_actor(raw.get("actor"))
        source_ref = raw.get("source_ref", "foyle")

        idx = seq.get(source_ref, 0)
        seq[source_ref] = idx + 1
        record_id = f"{source_ref}#e{idx}"

        text = _record_text(case_id, activity, timestamp, actor, status)
        records.append(NormalisedRecord(
            record_id=record_id,
            source_type=source_type,
            source_ref=source_ref,
            ingested_at=ingested_at,
            text=text,
            structured={"case_id": case_id, "activity": activity,
                        "timestamp": timestamp, "actor": actor, "status": status},
            events=[Event(case_id=str(case_id), activity=str(activity),
                          timestamp=str(timestamp), actor=actor, status=status,
                          source_ref=source_ref)],
            scrubbed_entities=actor_reps,
        ))
    return records


def normalise_text(text_rows: list[dict], source_type: str = "text") -> list[NormalisedRecord]:
    """One NormalisedRecord per paragraph, fully scrubbed. No events."""
    records: list[NormalisedRecord] = []
    ingested_at = _now_iso()
    per_file_counter: dict[str, int] = {}

    for raw in text_rows:
        source_ref = raw.get("source_ref", "unknown")
        idx = per_file_counter.get(source_ref, 0)
        per_file_counter[source_ref] = idx + 1

        scrubbed, reps = scrub_text(raw.get("text", ""))
        records.append(NormalisedRecord(
            record_id=f"{source_ref}#p{idx}",
            source_type=source_type,
            source_ref=source_ref,
            ingested_at=ingested_at,
            text=scrubbed,
            structured={},
            events=[],
            scrubbed_entities=reps,
        ))
    return records
