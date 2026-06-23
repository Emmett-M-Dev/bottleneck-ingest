"""Reader for the multi-sheet Foyle model (Phase B).

Reads the six wide, staff-maintained sheets + three driver emails under
data/synthetic/foyle/ and derives:

  - event_rows : canonical event dicts (case_id, activity, timestamp, actor,
                 status, source_ref) — one event log assembled FROM the sheets.
  - doc_rows   : free-text snippets (the 3 emails as paragraphs + one line per
                 sheet row) for the RAG corpus.

Cross-sheet resolution: the same student is re-keyed differently across sheets
("Lena Wagner" / "Wagner, Lena" / "Krüger"). `_canon_name` collapses those
variants to one stable case_id, so events from every sheet land on the right
case. The number of distinct sheets a case appears in is the real repetition
signal — that re-keying is the bottleneck, not a nuisance.

The genuine timestamps live in the invoices sheet (invoice -> payment), so that
pair carries the delay. Stage events without a real date in the sheets (request,
offer, confirmation) are placed at fixed offsets around the cohort arrival purely
to order the workflow map; only invoice/payment gaps drive delay detection.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

import pandas as pd

import config
from scrub.anonymise import scrub_actor

# Derived-date offsets (days BEFORE arrival) for stages with no real sheet date.
_OFFSETS = {
    "Request Received": 50,
    "Placement Offer": 40,
    "Placement Re-allocation": 35,
    "Booking Confirmed": 30,
}


def _canon_name(raw: str) -> str:
    """Collapse a re-keyed student name to a stable key.
    'Wagner, Lena' -> 'lena wagner'; strips accents/umlaut variants; lowercases."""
    if not raw:
        return ""
    s = str(raw).strip()
    if "," in s:                                  # surname-first variant
        last, first = (p.strip() for p in s.split(",", 1))
        s = f"{first} {last}"
    # Strip accents (ä->a), then fold the ae/ue/oe digraphs so the umlaut wobble
    # ('Schaefer' <-> 'Schäfer') resolves to one key.
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.lower().replace("ae", "a").replace("ue", "u").replace("oe", "o").replace("ß", "ss")
    return " ".join(s.split())


def _iso(value) -> str | None:
    if value in (None, ""):
        return None
    ts = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return None if pd.isna(ts) else ts.isoformat()


def _read(name: str) -> pd.DataFrame:
    df = pd.read_excel(config.FOYLE_DIR / name, dtype=str, engine="openpyxl")
    return df.fillna("")


def _derived(arrival_iso: str | None, stage: str) -> str | None:
    if not arrival_iso:
        return None
    a = pd.to_datetime(arrival_iso)
    return (a - timedelta(days=_OFFSETS[stage])).isoformat()


def read_foyle() -> tuple[list[dict], list[dict]]:
    event_rows: list[dict] = []
    doc_rows: list[dict] = []

    # case_id must NOT be the student's name (that is PII). Map each canonical
    # name to a stable pseudonym; the name itself never leaves this function.
    cid_map: dict[str, str] = {}

    def cid(canon: str) -> str:
        if canon not in cid_map:
            cid_map[canon] = f"FOY-S{len(cid_map) + 1:02d}"
        return cid_map[canon]

    # People-name vocabulary, gathered from the sheets, used to redact the free-text
    # snippets (NER alone misses bare names — same weakness the actor masking covers).
    people: set[str] = set()

    def note(*vals):
        for v in vals:
            if isinstance(v, str) and v.strip():
                people.add(v.strip())

    def emit(case_id, activity, timestamp, actor, status, ref):
        if case_id and activity and timestamp:
            event_rows.append({
                "case_id": case_id, "activity": activity, "timestamp": timestamp,
                "actor": actor or None, "status": status or None, "source_ref": ref,
            })

    # ── placements: request / offer / re-allocation / confirmed / arrival ─────
    pl = _read("placements.xlsx")
    arrival_by_case: dict[str, str] = {}
    for i, r in pl.iterrows():
        case = _canon_name(r.get("Student Name", ""))
        if not case:
            continue
        ref = f"placements.xlsx:{i + 2}"
        arr = _iso(r.get("Arrival"))
        arrival_by_case[case] = arr
        staff = r.get("Updated By") or r.get("Mentor")
        note(r.get("Student Name"), r.get("Accommodation"), r.get("Mentor"), r.get("Updated By"))
        emit(cid(case), "Request Received", _derived(arr, "Request Received"), staff, "requested",
             "email_1_group_request.txt")
        emit(cid(case), "Placement Offer", _derived(arr, "Placement Offer"), staff,
             r.get("Potential Placement"), ref)
        if "re-allocated" in (r.get("Notes", "") or "").lower():
            emit(cid(case), "Placement Re-allocation", _derived(arr, "Placement Re-allocation"),
                 staff, r.get("Notes"), ref)
        emit(cid(case), "Booking Confirmed", _derived(arr, "Booking Confirmed"), staff,
             r.get("Confirmed Placement") or "unconfirmed", ref)
        emit(cid(case), "Arrival", arr, staff, "arrived", ref)
        doc_rows.append({"source_ref": ref, "text":
            f"Placement: {r.get('Student Name')} ({r.get('Sector')}) with partner "
            f"{r.get('Partner')}, host {r.get('Accommodation')}, confirmed at "
            f"{r.get('Confirmed Placement') or 'TBC'}. {r.get('Notes')}".strip()})

    # ── host_families: re-allocation on drop-out / re-housing ─────────────────
    hf = _read("host_families.xlsx")
    for i, r in hf.iterrows():
        case = _canon_name(r.get("Student Hosted", ""))
        if not case:
            continue
        ref = f"host_families.xlsx:{i + 2}"
        status = (r.get("Status", "") or "").lower()
        arr = arrival_by_case.get(case)
        note(r.get("Student Hosted"), r.get("Host Family"), r.get("Updated By"))
        if "drop" in status or "re-hous" in status:
            emit(cid(case), "Placement Re-allocation",
                 _derived(arr, "Placement Re-allocation"), r.get("Host Family"), r.get("Status"), ref)
        doc_rows.append({"source_ref": ref, "text":
            f"Host family {r.get('Host Family')} ({r.get('Area')}) hosting "
            f"{r.get('Student Hosted')} — status {r.get('Status')}."})

    # ── work_placements: re-allocation on company decline ─────────────────────
    wp = _read("work_placements.xlsx")
    for i, r in wp.iterrows():
        case = _canon_name(r.get("Student Name", ""))
        if not case:
            continue
        ref = f"work_placements.xlsx:{i + 2}"
        arr = arrival_by_case.get(case)
        note(r.get("Student Name"), r.get("Mentor"), r.get("Updated By"))
        if "decline" in (r.get("Confirmed?", "") or "").lower():
            emit(cid(case), "Placement Re-allocation",
                 _derived(arr, "Placement Re-allocation"), r.get("Mentor"),
                 f"declined by {r.get('Company')}", ref)
        doc_rows.append({"source_ref": ref, "text":
            f"Work placement: {r.get('Student Name')} at {r.get('Company')} "
            f"({r.get('Sector')}) — {r.get('Confirmed?')}. "
            f"{('Re-allocated to ' + r.get('Re-allocated To')) if r.get('Re-allocated To') else ''}".strip()})

    # ── invoices: Invoice Issued -> Payment Received (the delay pair) ──────────
    iv = _read("invoices.xlsx")
    for i, r in iv.iterrows():
        case = _canon_name(r.get("Student Name", ""))
        if not case:
            continue
        ref = f"invoices.xlsx:{i + 2}"
        staff = r.get("Updated By")
        note(r.get("Student Name"), r.get("Updated By"))
        inv_iso = _iso(r.get("Invoice Date"))
        emit(cid(case), "Invoice Issued", inv_iso, staff, r.get("Invoice No"), ref)
        pending = "pend" in (r.get("Payment Received", "") or "").lower()
        pay_iso = _iso(r.get("Payment Date"))
        if pay_iso:
            emit(cid(case), "Payment Received", pay_iso, staff, "paid", ref)
        elif pending:
            # unpaid by arrival -> count as overdue (worst delay)
            emit(cid(case), "Payment Received", arrival_by_case.get(case), staff, "PENDING", ref)
        doc_rows.append({"source_ref": ref, "text":
            f"Invoice {r.get('Invoice No')} for {r.get('Student Name')} "
            f"(partner {r.get('Partner')}, GBP {r.get('Amount (GBP)')}): "
            f"payment {r.get('Payment Received') or 'unpaid'}."})

    # ── documents: re-request = repetition marker ─────────────────────────────
    dc = _read("documents.xlsx")
    for i, r in dc.iterrows():
        case = _canon_name(r.get("Student Name", ""))
        if not case:
            continue
        ref = f"documents.xlsx:{i + 2}"
        note(r.get("Student Name"), r.get("Updated By"))
        if (r.get("Re-requested?", "") or "").strip():
            ts = _iso(r.get("Date Requested")) or arrival_by_case.get(case)
            emit(cid(case), "Document Re-request", ts, r.get("Updated By"), r.get("Re-requested?"), ref)
        doc_rows.append({"source_ref": ref, "text":
            f"Documents for {r.get('Student Name')}: CV {r.get('CV') or '-'}, "
            f"motivation letter {r.get('Motivation Letter') or '-'}, consent "
            f"{r.get('Parental Consent') or '-'}. {r.get('Re-requested?')}".strip()})

    # ── accessni: background-check context (RAG only) ─────────────────────────
    ac = _read("accessni.xlsx")
    for i, r in ac.iterrows():
        ref = f"accessni.xlsx:{i + 2}"
        note(r.get("Student Name"))
        doc_rows.append({"source_ref": ref, "text":
            f"AccessNI {r.get('Check Type')} for {r.get('Student Name')} "
            f"({r.get('Sector')}): {r.get('Status')}."})

    # ── emails: split into paragraphs for the RAG corpus ──────────────────────
    email_dir = config.FOYLE_DIR / "emails"
    for path in sorted(email_dir.glob("*.txt")):
        body = path.read_text(encoding="utf-8")
        # Capture display names from the headers (e.g. "From: Niamh Kelly <...>").
        for m in re.finditer(r"^(?:From|To):\s*([^<\n]+?)\s*<", body, re.MULTILINE):
            note(m.group(1))
        # Capture roster names ("  1. Felix Becker, age 17, ...") in case a name
        # only ever appears wobbled in the sheets.
        for m in re.finditer(r"^\s*\d+\.\s*([^,\n]+?),\s*age", body, re.MULTILINE):
            note(m.group(1))
        for para in body.split("\n\n"):
            para = para.strip()
            if para:
                doc_rows.append({"source_ref": path.name, "text": para})

    # ── redact every gathered person name from the free-text snippets ─────────
    # Done explicitly (not via NER) so a bare name can never leak; emails/phones/
    # postcodes are still caught downstream by scrub_text in normalise_text.
    people_sorted = sorted(people, key=len, reverse=True)
    for d in doc_rows:
        text = d["text"]
        for name in people_sorted:
            if name in text:
                text = text.replace(name, scrub_actor(name)[0])
        d["text"] = text

    return event_rows, doc_rows
