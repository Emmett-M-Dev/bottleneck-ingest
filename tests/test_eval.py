"""Scoring arithmetic for the mapping eval (pure functions, no artefacts)."""

from __future__ import annotations

from eval.score_mapping import score_condition

_TRUTH = {
    ("a.xlsx", "Sheet1"): {"role": "events", "include": True,
                           "columns": {"Ref": "case_id", "Step": "activity",
                                       "Colour": None}},
    ("b.xlsx", "Sheet1"): {"role": "ignore", "include": False, "columns": {}},
}


def _pred(role_b="ignore", include_b=False, ref="case_id", colour=None) -> dict:
    return {
        ("a.xlsx", "Sheet1"): {"role": "events", "include": True,
                               "columns": {"Ref": ref, "Step": "activity",
                                           "Colour": colour}},
        ("b.xlsx", "Sheet1"): {"role": role_b, "include": include_b,
                               "columns": {"Name": None}},
    }


def test_perfect_prediction_scores_one() -> None:
    s = score_condition(_TRUTH, _pred())
    assert s["role_accuracy"] == 1.0
    assert s["include_accuracy"] == 1.0
    assert s["column_exact_accuracy"] == 1.0
    assert s["column_f1"] == 1.0
    assert s["column_errors"] == 0


def test_wrong_field_hits_precision_and_recall() -> None:
    # Ref mapped to the wrong field: one FP and one FN
    s = score_condition(_TRUTH, _pred(ref="status"))
    assert s["column_errors"] == 1
    assert s["column_precision"] == 0.5   # 1 TP / (1 TP + 1 FP)
    assert s["column_recall"] == 0.5      # 1 TP / (1 TP + 1 FN)


def test_spurious_mapping_is_a_false_positive_only() -> None:
    # Colour (truly unmapped) predicted as a field: precision drops, recall intact
    s = score_condition(_TRUTH, _pred(colour="status"))
    assert s["column_errors"] == 1
    assert s["column_precision"] == round(2 / 3, 3)
    assert s["column_recall"] == 1.0


def test_role_and_include_misses_counted() -> None:
    s = score_condition(_TRUTH, _pred(role_b="reference", include_b=True))
    assert s["role_accuracy"] == 0.5
    assert s["include_accuracy"] == 0.5
