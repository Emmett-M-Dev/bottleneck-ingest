"""The data contract. Everything downstream consumes these shapes.

Both readers (excel/text/sheets) and the future detection + RAG layers agree on
`Event` and `NormalisedRecord`. Plain dataclasses with explicit to/from-dict so the
shapes serialise cleanly to JSONL / Parquet without pulling in a validation library.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Event:
    case_id:    str
    activity:   str
    timestamp:  str           # ISO 8601
    actor:      str | None    # scrubbed to placeholder
    status:     str | None
    source_ref: str           # filename or "sheets:{sheet_id}:{row}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(
            case_id=d["case_id"],
            activity=d["activity"],
            timestamp=d["timestamp"],
            actor=d.get("actor"),
            status=d.get("status"),
            source_ref=d["source_ref"],
        )


@dataclass
class NormalisedRecord:
    record_id:         str
    source_type:       str    # "excel" | "text" | "sheets"
    source_ref:        str
    ingested_at:       str    # ISO 8601
    text:              str    # scrubbed, used for embedding
    structured:        dict   # scrubbed key-value fields
    events:            list[Event] = field(default_factory=list)
    scrubbed_entities: list[dict] = field(default_factory=list)  # [{type, placeholder}]

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "ingested_at": self.ingested_at,
            "text": self.text,
            "structured": self.structured,
            "events": [e.to_dict() for e in self.events],
            "scrubbed_entities": self.scrubbed_entities,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NormalisedRecord":
        return cls(
            record_id=d["record_id"],
            source_type=d["source_type"],
            source_ref=d["source_ref"],
            ingested_at=d["ingested_at"],
            text=d["text"],
            structured=dict(d.get("structured", {})),
            events=[Event.from_dict(e) for e in d.get("events", [])],
            scrubbed_entities=list(d.get("scrubbed_entities", [])),
        )
