"""Reads any .txt in a folder, split into paragraphs. No structure extraction —
the text feeds RAG as-is after scrubbing.
"""

from __future__ import annotations

from pathlib import Path


def _read_one(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in text.split("\n\n")]
    return [{"text": p, "source_ref": path.name} for p in paragraphs if p]


def read_text_folder(folder) -> list[dict]:
    """Read every .txt in `folder`. Returns [{"text", "source_ref"}] per paragraph."""
    folder = Path(folder)
    rows: list[dict] = []
    for path in sorted(folder.glob("*.txt")):
        rows.extend(_read_one(path))
    return rows
