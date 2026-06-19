"""Pipeline tests: normalise (mapping, ISO timestamp, actor scrub, incomplete rows)
and chunking. Embedding is exercised in the end-to-end run, not here (keeps the
unit suite fast and offline)."""

from __future__ import annotations

from models import NormalisedRecord
from pipeline.chunk import chunk_records
from pipeline.normalise import normalise_structured, normalise_text


def test_normalise_structured_maps_and_extracts_event() -> None:
    raw = [{
        "Booking Ref": "BR-0001",
        "Stage": "Booking Confirmation",
        "Date": "05/01/2026",          # dd/mm/yyyy
        "Handled By": "Sarah Jones",
        "Status": "done",
        "_source_ref": "tracker.xlsx:2",
    }]
    recs = normalise_structured(raw, source_type="excel")
    assert len(recs) == 1
    rec = recs[0]
    assert rec.source_type == "excel"
    assert len(rec.events) == 1
    ev = rec.events[0]
    assert ev.case_id == "BR-0001"
    assert ev.activity == "Booking Confirmation"   # label preserved, not scrubbed
    assert ev.timestamp.startswith("2026-01-05")   # dayfirst parse -> ISO
    assert "Sarah Jones" not in (ev.actor or "")    # actor scrubbed
    assert ev.actor.startswith("[PERSON")


def test_normalise_structured_incomplete_row_no_event(caplog) -> None:
    raw = [{"Booking Ref": "BR-0002", "Stage": None, "Date": None, "_source_ref": "t.xlsx:9"}]
    recs = normalise_structured(raw, source_type="excel")
    assert len(recs) == 1
    assert recs[0].events == []          # incomplete -> no event, but record kept


def test_normalise_text_scrubs_and_has_no_events() -> None:
    rows = [{"text": "Call Sarah Jones on 07700 900123 about delays.", "source_ref": "ops.txt"}]
    recs = normalise_text(rows)
    assert recs[0].source_type == "text"
    assert recs[0].events == []
    assert "Sarah Jones" not in recs[0].text
    assert "07700 900123" not in recs[0].text


def test_chunk_records_overlap_and_metadata() -> None:
    long_text = " ".join(f"word{i}" for i in range(200))  # well over CHUNK_SIZE chars
    rec = NormalisedRecord(
        record_id="rec-1", source_type="text", source_ref="ops.txt",
        ingested_at="2026-06-16T00:00:00+00:00", text=long_text, structured={},
    )
    chunks = chunk_records([rec])
    assert len(chunks) >= 2
    assert all(c["metadata"]["record_id"] == "rec-1" for c in chunks)
    assert [c["metadata"]["chunk_index"] for c in chunks] == list(range(len(chunks)))
