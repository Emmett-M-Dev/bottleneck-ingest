"""Ingestion CLI.

    python ingest.py --source local         # data/synthetic/ (xlsx + txt)
    python ingest.py --source sheets        # live mock Google Sheet
    python ingest.py --source all           # both, deduplicated by source_ref
    python ingest.py --source foyle         # six local Foyle sheets (data/synthetic/foyle/)
    python ingest.py --source foyle-tracker         # streamlined placement tracker (local xlsx)
    python ingest.py --source foyle-tracker-sheets  # the same tracker live from a Drive sheet
    python ingest.py --source foyle-sheets  # the same six sheets live from a Drive folder

Each mode: read -> scrub -> normalise -> write parquet + jsonl -> embed into ChromaDB.
The local and sheets paths produce identical output shapes, so downstream detection
and RAG cannot tell mock from real.
"""

from __future__ import annotations

import os

# Set before any native lib (torch via spaCy/thinc/blis, chromadb/hnswlib) loads —
# their OpenMP/BLAS runtimes collide and segfault on Windows at scale unless pinned
# to a single thread. Must precede the imports below.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import json
import logging

import pandas as pd

import config
from models import NormalisedRecord
from pipeline.chunk import chunk_records
from pipeline.embed import embed_chunks, reset_collection
from pipeline.normalise import normalise_foyle_events, normalise_structured, normalise_text
from readers.excel_reader import read_excel_folder
from readers.text_reader import read_text_folder

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingest")


def _read_local() -> tuple[list[NormalisedRecord], str]:
    excel_rows = read_excel_folder(config.DATA_SYNTHETIC)
    text_rows = read_text_folder(config.DATA_SYNTHETIC)
    n_xlsx = len(sorted(config.DATA_SYNTHETIC.glob("*.xlsx")))
    n_txt = len(sorted(config.DATA_SYNTHETIC.glob("*.txt")))
    records = (
        normalise_structured(excel_rows, source_type="excel")
        + normalise_text(text_rows)
    )
    summary = (
        f"[local]  Read {len(excel_rows)} event rows from {n_xlsx} xlsx, "
        f"{len(text_rows)} paragraphs from {n_txt} txt files"
    )
    return records, summary


def _read_sheets() -> tuple[list[NormalisedRecord], str]:
    from readers.sheets_reader import read_sheet  # imported lazily so local mode needs no creds
    rows = read_sheet()
    records = normalise_structured(rows, source_type="sheets")
    summary = f"[sheets] Read {len(rows)} rows from Google Sheet (mock env)"
    return records, summary


def _read_foyle() -> tuple[list[NormalisedRecord], str]:
    from readers.foyle_reader import read_foyle  # lazy: only this path needs the foyle sheets
    event_rows, doc_rows = read_foyle()
    records = (
        normalise_foyle_events(event_rows)
        + normalise_text(doc_rows, source_type="foyle_text")
    )
    summary = (
        f"[foyle]  Derived {len(event_rows)} events from 6 sheets, "
        f"{len(doc_rows)} text snippets (sheet rows + 3 emails)"
    )
    return records, summary


def _read_foyle_tracker() -> tuple[list[NormalisedRecord], str]:
    from readers.foyle_tracker_reader import read_foyle_tracker  # lazy: only this path reads the tracker
    event_rows, doc_rows = read_foyle_tracker()
    records = (
        normalise_foyle_events(event_rows)
        + normalise_text(doc_rows, source_type="foyle_text")
    )
    summary = (
        f"[foyle-tracker] Derived {len(event_rows)} events from the placement tracker, "
        f"{len(doc_rows)} cohort snippets"
    )
    return records, summary


def _read_foyle_tracker_sheets() -> tuple[list[NormalisedRecord], str]:
    from readers.foyle_tracker_reader import read_foyle_tracker_sheets  # lazy: needs google libs + creds
    event_rows, doc_rows = read_foyle_tracker_sheets()
    records = (
        normalise_foyle_events(event_rows)
        + normalise_text(doc_rows, source_type="foyle_text")
    )
    summary = (
        f"[foyle-tracker-sheets] Derived {len(event_rows)} events from the live tracker "
        f"sheet, {len(doc_rows)} cohort snippets"
    )
    return records, summary


def _read_foyle_sheets() -> tuple[list[NormalisedRecord], str]:
    from readers.foyle_sheets_reader import read_foyle_sheets  # lazy: needs google libs + creds
    event_rows, doc_rows = read_foyle_sheets()
    records = (
        normalise_foyle_events(event_rows)
        + normalise_text(doc_rows, source_type="foyle_text")
    )
    summary = (
        f"[foyle-sheets] Derived {len(event_rows)} events from the Drive folder, "
        f"{len(doc_rows)} sheet-row snippets"
    )
    return records, summary


def _dedup(records: list[NormalisedRecord]) -> list[NormalisedRecord]:
    seen: dict[str, NormalisedRecord] = {}
    for rec in records:
        seen.setdefault(rec.record_id, rec)
    return list(seen.values())


def _write_records(records: list[NormalisedRecord]) -> int:
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    with config.RECORDS_PATH.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
    return len(records)


def _write_event_log(records: list[NormalisedRecord]) -> int:
    # pyarrow's Arrow C++ runtime is loaded here (lazily, on to_parquet). It must run
    # AFTER the chroma/hnswlib embedding step — sharing the process with Arrow during
    # the HNSW index build segfaults on Windows. The caller orders this last.
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    events = [e.to_dict() for rec in records for e in rec.events]
    df = pd.DataFrame(events, columns=["case_id", "activity", "timestamp", "actor", "status", "source_ref"])
    # fastparquet, not pyarrow: importing pyarrow loads the Arrow C++ runtime, which
    # segfaults when sharing the process with chroma/hnswlib + torch on Windows.
    df.to_parquet(config.EVENT_LOG_PATH, index=False, engine="fastparquet")
    return len(events)


def run(source: str) -> None:
    from scrub.anonymise import reset_actor_registry
    reset_actor_registry()  # deterministic actor placeholder numbering per run

    summaries: list[str] = []
    records: list[NormalisedRecord] = []

    if source in ("local", "all"):
        recs, summ = _read_local()
        records += recs
        summaries.append(summ)
    if source in ("sheets", "all"):
        recs, summ = _read_sheets()
        records += recs
        summaries.append(summ)
    if source == "foyle":
        recs, summ = _read_foyle()
        records += recs
        summaries.append(summ)
    if source == "foyle-tracker":
        recs, summ = _read_foyle_tracker()
        records += recs
        summaries.append(summ)
    if source == "foyle-tracker-sheets":
        recs, summ = _read_foyle_tracker_sheets()
        records += recs
        summaries.append(summ)
    if source == "foyle-sheets":
        recs, summ = _read_foyle_sheets()
        records += recs
        summaries.append(summ)

    if source == "all":
        records = _dedup(records)

    n_scrubbed = sum(len(rec.scrubbed_entities) for rec in records)
    # Order matters: jsonl + embed (chroma/hnswlib) before the parquet write, because
    # pyarrow's Arrow runtime cannot coexist with the HNSW index build on Windows.
    n_records = _write_records(records)
    reset_collection()  # rebuild the vector store from scratch for this source
    n_chunks = embed_chunks(chunk_records(records))
    n_events = _write_event_log(records)

    print()
    for line in summaries:
        print(line)
    print(f"Scrubbed: {n_scrubbed} entities replaced")
    print(f"Records:  {n_records} written to {config.RECORDS_PATH.relative_to(config.ROOT)}")
    print(f"Events:   {n_events} rows written to {config.EVENT_LOG_PATH.relative_to(config.ROOT)}")
    print(f"Embedded: {n_chunks} chunks into ChromaDB collection '{config.CHROMA_COLLECTION}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="SME ops ingestion pipeline")
    parser.add_argument(
        "--source",
        choices=["local", "sheets", "all", "foyle", "foyle-tracker",
                 "foyle-tracker-sheets", "foyle-sheets"],
        default="local",
    )
    args = parser.parse_args()
    run(args.source)


if __name__ == "__main__":
    main()
