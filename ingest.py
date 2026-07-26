"""Ingestion CLI.

    python ingest.py --source local         # data/synthetic/ (xlsx + txt)
    python ingest.py --source sheets        # live mock Google Sheet
    python ingest.py --source all           # both, deduplicated by source_ref
    python ingest.py --source foyle         # six local Foyle sheets (data/synthetic/foyle/)
    python ingest.py --source foyle-tracker         # streamlined placement tracker (local xlsx)
    python ingest.py --source foyle-tracker-sheets  # the same tracker live from a Drive sheet
    python ingest.py --source foyle-sheets  # the same six sheets live from a Drive folder
    python ingest.py --source messy --profile foyle   # messy drive via APPROVED mapping

The messy path enforces the HITL gate ordering: it hard-errors unless a
human-approved mapping exists (audit agent proposes -> dashboard Mapping
Review approves -> only then does ingestion run).

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
from pathlib import Path

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


def _read_messy(profile: str, mapping_path=None,
                drive=None) -> tuple[list[NormalisedRecord], str]:
    from audit.schemas import load_approved  # lazy: pydantic only on this path
    from readers.mapped_reader import read_mapped

    mapping_path = mapping_path or config.approved_mapping_path(profile)
    if not mapping_path.exists():
        raise SystemExit(
            f"No approved mapping for profile '{profile}' ({mapping_path}).\n"
            f"The messy path only ingests through a human-approved mapping:\n"
            f"    1. python -m audit.run --profile {profile}\n"
            f"    2. approve it in the dashboard's Mapping Review tab\n"
            f"    3. re-run this command"
        )
    approved = load_approved(mapping_path)
    # A later snapshot of the same drive reuses the same approved mapping —
    # that is what makes week-on-week re-analysis possible without a second
    # trip through the mapping gate.
    drive = Path(drive) if drive else config.MESSY_PROFILES[profile]["dir"]
    event_rows, doc_rows = read_mapped(drive, approved)
    records = (
        normalise_foyle_events(event_rows, source_type="messy")
        + normalise_text(doc_rows, source_type="messy_text")
    )
    n_files = sum(1 for f in approved.files if f.include and f.role != "ignore")
    summary = (
        f"[messy:{profile}] Derived {len(event_rows)} events + {len(doc_rows)} snippets "
        f"from {n_files} mapped files in {drive.name}/ "
        f"(approved {approved.approved_at[:19]})"
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


def event_log_owner_path():
    """Which profile/source the current event log belongs to.

    `outputs/event_log.parquet` is a single global file that every profile
    overwrites in turn. Anything reading it downstream needs to know whose data
    it is currently holding — without this marker a consumer happily builds a
    joinery action queue out of advisory events."""
    return config.OUTPUTS / "event_log_profile.txt"


def _write_event_log_owner(source: str, profile: str | None) -> None:
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    event_log_owner_path().write_text(profile or source, encoding="utf-8")


def _write_event_log(records: list[NormalisedRecord]) -> int:
    # pyarrow's Arrow C++ runtime is loaded here (lazily, on to_parquet). It must run
    # AFTER the chroma/hnswlib embedding step — sharing the process with Arrow during
    # the HNSW index build segfaults on Windows. The caller orders this last.
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    events = [e.to_dict() for rec in records for e in rec.events]
    df = pd.DataFrame(events, columns=["case_id", "activity", "timestamp", "actor",
                                       "status", "source_ref", "value"])
    # fastparquet types an all-None column as object and then refuses it; the
    # canonical type for the optional money column is float either way.
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
    # fastparquet, not pyarrow: importing pyarrow loads the Arrow C++ runtime, which
    # segfaults when sharing the process with chroma/hnswlib + torch on Windows.
    df.to_parquet(config.EVENT_LOG_PATH, index=False, engine="fastparquet")
    return len(events)


def run(source: str, profile: str | None = None, mapping_path=None,
        drive=None) -> None:
    from scrub.anonymise import reset_actor_registry
    reset_actor_registry()  # deterministic actor placeholder numbering per run

    summaries: list[str] = []
    records: list[NormalisedRecord] = []

    if source == "messy":
        if not profile:
            raise SystemExit("--source messy requires --profile")
        recs, summ = _read_messy(profile, mapping_path, drive)
        records += recs
        summaries.append(summ)

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
    _write_event_log_owner(source, profile)

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
                 "foyle-tracker-sheets", "foyle-sheets", "messy"],
        default="local",
    )
    parser.add_argument("--profile", choices=sorted(config.MESSY_PROFILES),
                        default=None, help="SME profile for --source messy")
    parser.add_argument("--mapping", type=Path, default=None,
                        help="approved mapping JSON (default: mappings/approved_<profile>.json)")
    parser.add_argument("--drive", type=Path, default=None,
                        help="a later snapshot of the same drive (default: the "
                             "profile's configured folder) — re-analysed through "
                             "the SAME approved mapping, no second mapping gate")
    args = parser.parse_args()
    run(args.source, profile=args.profile, mapping_path=args.mapping,
        drive=args.drive)


if __name__ == "__main__":
    main()
