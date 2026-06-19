"""PII scrubbing — runs before anything is stored or embedded.

No raw PII ever reaches ChromaDB, records.jsonl, or event_log.parquet. spaCy NER
replaces PERSON/ORG/GPE/LOC/FAC/NORP with stable placeholders; regex catches
emails, phone numbers, and UK postcodes. Dates and numbers pass through unchanged
(those labels are not in SCRUB_ENTITIES and the regexes don't match them).

Public API:
    scrub_text(text) -> (scrubbed_str, replacements)
    scrub_dict(record) -> (scrubbed_dict, replacements)

`replacements` is a list of {type, placeholder}. Originals are never returned or stored.
"""

from __future__ import annotations

import re

import spacy

from config import SCRUB_ENTITIES, SPACY_MODEL

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+44\s?|\b0)\d[\d\s-]{7,11}\d")
POSTCODE_RE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")

_REGEX_TYPES = [("EMAIL", EMAIL_RE), ("PHONE", PHONE_RE), ("POSTCODE", POSTCODE_RE)]

_nlp = None


def _get_nlp():
    global _nlp
    if _nlp is None:
        _nlp = spacy.load(SPACY_MODEL, disable=["lemmatizer", "tagger", "parser"])
    return _nlp


class _Scrubber:
    """Holds placeholder state so identical originals map to the same placeholder
    across a single document (a text or all string fields of one record)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._seen: dict[tuple[str, str], str] = {}  # (type, original) -> placeholder
        self.replacements: list[dict] = []

    def _placeholder(self, ent_type: str, original: str) -> str:
        key = (ent_type, original)
        if key in self._seen:
            return self._seen[key]
        self._counters[ent_type] = self._counters.get(ent_type, 0) + 1
        ph = f"[{ent_type}_{self._counters[ent_type]}]"
        self._seen[key] = ph
        self.replacements.append({"type": ent_type, "placeholder": ph})
        return ph

    def scrub(self, text: str) -> str:
        if not text:
            return text

        spans: list[tuple[int, int, str, str]] = []  # (start, end, type, original)

        # Regex PII (email / phone / postcode)
        for ent_type, rx in _REGEX_TYPES:
            for m in rx.finditer(text):
                spans.append((m.start(), m.end(), ent_type, m.group()))

        # spaCy named entities
        doc = _get_nlp()(text)
        for ent in doc.ents:
            if ent.label_ in SCRUB_ENTITIES:
                spans.append((ent.start_char, ent.end_char, ent.label_, ent.text))

        if not spans:
            return text

        # Greedy non-overlapping selection: earliest start, then longest match.
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        chosen: list[tuple[int, int, str, str]] = []
        last_end = -1
        for start, end, ent_type, original in spans:
            if start >= last_end:
                chosen.append((start, end, ent_type, original))
                last_end = end

        out: list[str] = []
        cursor = 0
        for start, end, ent_type, original in chosen:
            out.append(text[cursor:start])
            out.append(self._placeholder(ent_type, original.strip()))
            cursor = end
        out.append(text[cursor:])
        return "".join(out)


def scrub_text(text: str) -> tuple[str, list[dict]]:
    s = _Scrubber()
    scrubbed = s.scrub(text)
    return scrubbed, s.replacements


def scrub_dict(record: dict) -> tuple[dict, list[dict]]:
    """Scrub all string values in a record, sharing placeholder state across fields."""
    s = _Scrubber()
    out: dict = {}
    for key, value in record.items():
        out[key] = s.scrub(value) if isinstance(value, str) else value
    return out, s.replacements
