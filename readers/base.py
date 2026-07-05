"""The canonical layer contract — what every reader ultimately produces.

The pipeline's fixed internal representation is `models.Event`
(case_id / activity / timestamp / actor / status / source_ref) plus
`models.NormalisedRecord`. Everything downstream — normalisation, scrubbing,
embedding, detection, export, the dashboard — depends only on that shape.
Everything per-SME lives in a thin adapter that maps one organisation's
specific file mess into it. Onboarding a new SME means writing (or, on the
`--source messy` path, *inferring and approving*) a new adapter; the core
never changes.

Two adapter families exist:

Pre-canonical connectors — return ``(event_rows, doc_rows)`` where each event
row is already canonical (``{case_id, activity, timestamp, actor, status,
source_ref}``) and each doc row is ``{source_ref, text}``. These conform to
the `Connector` protocol below:

    readers/foyle_reader.py          six-sheet Foyle model (local xlsx)
    readers/foyle_sheets_reader.py   same, live from Google Drive
    readers/foyle_tracker_reader.py  per-cohort placement tracker
    readers/mapped_reader.py         generic — driven by an approved mapping

Alias-resolved raw readers — return raw dicts with their original headers and
let `mapping.resolve_columns` + `pipeline.normalise` do the canonicalisation
via `config.COLUMN_MAP`:

    readers/excel_reader.py
    readers/sheets_reader.py

New readers should be pre-canonical connectors: the alias path only handles
single event-log-shaped sheets, while a connector can derive events from any
structure.
"""

from __future__ import annotations

from typing import Protocol

# One canonical event row: {case_id, activity, timestamp, actor, status, source_ref}
EventRow = dict
# One free-text snippet for the RAG corpus: {source_ref, text}
DocRow = dict


class Connector(Protocol):
    """A per-SME adapter: reads that SME's files, returns canonical rows."""

    def read(self) -> tuple[list[EventRow], list[DocRow]]: ...
