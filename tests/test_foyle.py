"""Tests for the multi-sheet Foyle model (Phase B): cross-sheet name resolution
and the Foyle-specific detector (invoice->payment delay + presence markers)."""

from __future__ import annotations

import pandas as pd

from detection.detect import detect_foyle
from readers.foyle_reader import _canon_name, _derive
from readers.foyle_sheets_reader import _match_key


def _df(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["case_id", "activity", "timestamp", "actor", "status", "source_ref"])
    df["stage"] = df["activity"].str.strip().str.lower()
    df["ts"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


def _by_id(results):
    return {b.id: b for b in results}


def test_canon_name_folds_rekeyed_variants() -> None:
    # surname-first, accent strip, and ae/ue digraph folding all resolve to one key
    assert _canon_name("Wagner, Lena") == "lena wagner"
    assert _canon_name("Lena Wagner") == "lena wagner"
    assert _canon_name("Schäfer") == _canon_name("Schaefer")
    assert _canon_name("Krüger") == _canon_name("Krueger")


def test_foyle_delay_is_invoice_to_payment_gap() -> None:
    df = _df([
        # late: 30-day invoice->payment gap -> flagged
        ("S1", "Invoice Issued", "2026-03-01", None, "INV-1", "invoices.xlsx:2"),
        ("S1", "Payment Received", "2026-03-31", None, "paid", "invoices.xlsx:2"),
        # prompt: 5-day gap -> not flagged
        ("S2", "Invoice Issued", "2026-03-01", None, "INV-2", "invoices.xlsx:3"),
        ("S2", "Payment Received", "2026-03-06", None, "paid", "invoices.xlsx:3"),
    ])
    res = _by_id(detect_foyle(df))
    assert res["BN001"].affected_cases == ["S1"]
    assert res["BN001"].metric_value >= 21


def test_foyle_repetition_and_rework_by_presence() -> None:
    df = _df([
        ("S1", "Document Re-request", "2026-03-02", None, "chased", "documents.xlsx:2"),
        ("S2", "Placement Re-allocation", "2026-03-03", None, "host dropped", "host_families.xlsx:2"),
        ("S3", "Arrival", "2026-04-07", None, "arrived", "placements.xlsx:2"),
    ])
    res = _by_id(detect_foyle(df))
    assert res["BN002"].affected_cases == ["S1"]   # repetition: Document Re-request
    assert res["BN003"].affected_cases == ["S2"]   # rework: Placement Re-allocation


def test_foyle_always_returns_three_bottlenecks() -> None:
    res = _by_id(detect_foyle(_df([("S1", "Arrival", "2026-04-07", None, "arrived", "placements.xlsx:2")])))
    assert set(res) == {"BN001", "BN002", "BN003"}
    assert all(b.affected_count == 0 for b in res.values())


def test_match_key_normalises_sheet_titles() -> None:
    assert _match_key("placements") == "placements"
    assert _match_key("Host Families") == "host_families"
    assert _match_key("work-placements") == "work_placements"
    assert _match_key("Random Sheet") is None


def test_derive_is_source_agnostic_and_redacts_names() -> None:
    # Same student re-keyed across three sheets (incl. surname-first) -> one case;
    # the derivation is identical whether frames came from xlsx or Drive.
    frames = {
        "placements": pd.DataFrame([{
            "Student Name": "Lena Wagner", "Arrival": "07.04.2026", "Updated By": "Aine Murray",
            "Mentor": "Joy McCallion", "Accommodation": "Bernadette Coyle", "Sector": "Education",
            "Partner": "EduMobil Bremen", "Potential Placement": "Riverside Primary School",
            "Confirmed Placement": "St Brigids PS", "Notes": "re-allocated from 1st pref",
        }]),
        "invoices": pd.DataFrame([{
            "Student Name": "Wagner, Lena", "Updated By": "Paul Doherty", "Invoice Date": "01.03.2026",
            "Invoice No": "INV-2600", "Payment Received": "yes", "Payment Date": "05.04.2026",
            "Partner": "EduMobil Bremen", "Amount (GBP)": "1450",
        }]),
        "documents": pd.DataFrame([{
            "Student Name": "Lena Wagner", "Updated By": "Niamh Kelly", "Re-requested?": "Yes - chased",
            "Date Requested": "05.03.2026", "CV": "Yes", "Motivation Letter": "Yes", "Parental Consent": "N/A",
        }]),
    }
    events, docs = _derive(frames, email_texts=None)

    acts = {e["activity"] for e in events}
    assert {"Invoice Issued", "Payment Received", "Document Re-request", "Placement Re-allocation"} <= acts
    # the re-keyed variants resolve to a single pseudonymous case
    assert {e["case_id"] for e in events} == {"FOY-S01"}
    # the real name never appears in a case_id or a (redacted) snippet
    blob = " ".join(e["case_id"] for e in events) + " " + " ".join(d["text"] for d in docs)
    assert "Lena Wagner" not in blob and "Wagner, Lena" not in blob
