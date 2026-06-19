"""Splits NormalisedRecord.text into overlapping chunks for embedding.

Each chunk carries record_id + source_ref (and source_type, ingested_at) in its
metadata so retrieved chunks trace back to their source.
"""

from __future__ import annotations

from config import CHUNK_OVERLAP, CHUNK_SIZE
from models import NormalisedRecord


def _split(text: str, size: int, overlap: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    step = max(1, size - overlap)
    pieces = []
    for start in range(0, len(text), step):
        piece = text[start:start + size].strip()
        if piece:
            pieces.append(piece)
        if start + size >= len(text):
            break
    return pieces


def chunk_records(records: list[NormalisedRecord]) -> list[dict]:
    """Return list of {"text", "metadata"} with stable per-record chunk indices."""
    chunks: list[dict] = []
    for rec in records:
        for i, piece in enumerate(_split(rec.text, CHUNK_SIZE, CHUNK_OVERLAP)):
            chunks.append({
                "text": piece,
                "metadata": {
                    "record_id": rec.record_id,
                    "source_ref": rec.source_ref,
                    "source_type": rec.source_type,
                    "ingested_at": rec.ingested_at,
                    "chunk_index": i,
                },
            })
    return chunks
