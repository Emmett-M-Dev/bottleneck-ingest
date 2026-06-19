"""Round-trip tests for the data contract. No I/O, no pipeline."""

from __future__ import annotations

from models import Event, NormalisedRecord


def _sample_event() -> Event:
    return Event(
        case_id="BR-0001",
        activity="Booking Confirmation",
        timestamp="2026-01-12T09:00:00",
        actor="[PERSON_1]",
        status="done",
        source_ref="bookings_tracker.xlsx",
    )


def test_event_round_trip() -> None:
    ev = _sample_event()
    assert Event.from_dict(ev.to_dict()) == ev


def test_event_round_trip_with_none_fields() -> None:
    ev = Event(
        case_id="BR-0002",
        activity="Enquiry",
        timestamp="2026-01-03T10:00:00",
        actor=None,
        status=None,
        source_ref="sheets:abc:5",
    )
    assert Event.from_dict(ev.to_dict()) == ev


def test_normalised_record_round_trip() -> None:
    rec = NormalisedRecord(
        record_id="rec-1",
        source_type="excel",
        source_ref="bookings_tracker.xlsx:2",
        ingested_at="2026-06-16T12:00:00+00:00",
        text="Booking confirmation for [ORG_1] handled by [PERSON_1].",
        structured={"case_id": "BR-0001", "activity": "Booking Confirmation"},
        events=[_sample_event()],
        scrubbed_entities=[{"type": "PERSON", "placeholder": "[PERSON_1]"}],
    )
    restored = NormalisedRecord.from_dict(rec.to_dict())
    assert restored == rec
    assert restored.events[0] == _sample_event()


def test_normalised_record_defaults_empty_collections() -> None:
    rec = NormalisedRecord(
        record_id="rec-2",
        source_type="text",
        source_ref="ops_notes_jan.txt",
        ingested_at="2026-06-16T12:00:00+00:00",
        text="Confirmations are dragging again.",
        structured={},
    )
    assert rec.events == []
    assert rec.scrubbed_entities == []
    assert NormalisedRecord.from_dict(rec.to_dict()) == rec
