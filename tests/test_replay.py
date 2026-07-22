"""Longitudinal-replay tests on the pure halves (no chroma, no model, no
network): pattern-visibility timestamps, the oracle approver's majority rule,
learned-retrieval stats, decision-id stability across ticks, the replay's own
learned file (RES-RPL ids, dedup), figure smoke tests, and light imports.
"""

from __future__ import annotations

import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config
from detection.detect import DetectedBottleneck
from eval import replay
from synthetic.generate_stream import KINDS, RECIPES, _visible_ts

_MARKERS = {"delay_stage": "Booking Confirmed",
            "repetition_stage": "Document Collection",
            "rework_stage": "Placement Offer"}


def _ev(stage: str, day: int) -> dict:
    return {"activity": stage, "ts": datetime(2026, 3, day)}


def test_visible_ts_first_occurrence_for_delay() -> None:
    evs = [_ev("Placement Offer", 1), _ev("booking confirmed ", 15)]
    assert _visible_ts(evs, "delay", _MARKERS) == datetime(2026, 3, 15)


def test_visible_ts_second_occurrence_for_repetition_and_rework() -> None:
    evs = [_ev("Document Collection", 3), _ev("DOCUMENT COLLECTION", 6),
           _ev("Placement Offer", 2), _ev("placement offer", 9)]
    assert _visible_ts(evs, "repetition", _MARKERS) == datetime(2026, 3, 6)
    assert _visible_ts(evs, "rework", _MARKERS) == datetime(2026, 3, 9)


def test_every_recipe_injection_completes() -> None:
    """Each profile's builder + markers yield a visibility timestamp for every
    pattern kind — the per-tick ground truth can always be computed."""
    for profile, recipe in RECIPES.items():
        markers = config.MESSY_PROFILES[profile]["markers"]
        for kind in KINDS:
            random.seed(1)
            evs = recipe["builder"]("T-1", datetime(2026, 3, 2),
                                    **{k: k == kind for k in KINDS})
            assert _visible_ts(evs, kind, markers) >= datetime(2026, 3, 2)


def _bn(cases: list[str], type_: str = "delay") -> DetectedBottleneck:
    return DetectedBottleneck(id="BN001", type=type_, stage="Booking Confirmed",
                              affected_cases=cases, metric_label="avg_delay_days",
                              metric_value=14.0)


def test_oracle_approves_majority_rejects_tie_and_minority() -> None:
    truth = {"delay": {"A", "B"}}
    assert replay.oracle_decide(_bn(["A", "B", "C"]), truth) is True
    assert replay.oracle_decide(_bn(["A", "C"]), truth) is False      # tie
    assert replay.oracle_decide(_bn(["C", "D", "E"]), truth) is False
    assert replay.oracle_decide(_bn(["A"], "rework"), truth) is False  # wrong type


def test_retrieval_stats_rank_and_relevance() -> None:
    retrieved = [
        {"source": "resolutions:corpus", "bottleneck_type": "delay"},
        {"source": "resolutions:learned", "bottleneck_type": "rework"},
        {"source": "resolutions:learned", "bottleneck_type": "delay"},
    ]
    stats = replay._retrieval_stats(_bn(["A"]), retrieved)
    assert stats == {"learned_in_topk": True, "learned_rank": 2,
                     "relevant_learned_in_topk": True}
    none = replay._retrieval_stats(_bn(["A"]), retrieved[:1])
    assert none == {"learned_in_topk": False, "learned_rank": None,
                    "relevant_learned_in_topk": False}


class _Diag:
    class suggested_fix:
        summary, steps, rationale = "Add an SLA", ["step"], "because"
    diagnosis = "cases stall"
    confidence = 0.8


def test_decision_id_stable_across_ticks() -> None:
    d3 = replay._decision_record("foyle", _bn(["A"]), _Diag, True, 3, "2026-03-23")
    d7 = replay._decision_record("foyle", _bn(["A", "B"]), _Diag, True, 7, "2026-04-20")
    assert d3["decision_id"] == d7["decision_id"]  # one fix, approved once
    assert d3["action"] == "approve"
    assert replay._decision_record("foyle", _bn(["A"]), _Diag, False, 3,
                                   "2026-03-23")["action"] == "reject"


def test_learn_approved_rpl_ids_and_dedup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(replay, "replay_learned_path",
                        lambda p: tmp_path / f"learned_{p}.json")
    decision = replay._decision_record("foyle", _bn(["A"]), _Diag, True, 3,
                                       "2026-03-23")
    entry = replay._learn_approved("foyle", decision)
    assert entry is not None
    assert entry["resolution_id"] == "RES-RPL-FOY-001"
    assert entry["source"] == "learned"
    assert replay._learn_approved("foyle", decision) is None  # decision replayed
    other = replay._decision_record("foyle", _bn(["B"], "rework"), _Diag, True,
                                    4, "2026-03-30")
    assert replay._learn_approved("foyle", other)["resolution_id"] == "RES-RPL-FOY-002"


def _tick(t: int, f1: float, hit: float | None, corpus: int) -> dict:
    prf = {"tp": 2, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": f1}
    return {"record": "tick", "tick": t, "cutoff": "2026-03-23",
            "cases_visible": 5, "events_visible": 30,
            "detection": {"delay": prf, "repetition": prf, "rework": prf,
                          "macro_f1": f1},
            "findings": 2, "gate": {"approved": 2, "rejected": 1, "learned_new": 1},
            "retrieval": {"learned_hit_rate": hit,
                          "relevant_learned_hit_rate": hit,
                          "mean_learned_rank": 1.5 if hit else None,
                          "learned_corpus_size": corpus},
            "modelled_cost": 500, "per_finding": []}


def test_plot_figures_build_from_tick_records() -> None:
    from eval.plot_replay import fig_detection, fig_gate, fig_learning

    ticks = [_tick(1, 0.5, None, 0), _tick(2, 0.8, 0.5, 2), _tick(3, 1.0, 1.0, 3)]
    for fn in (fig_detection, fig_learning, fig_gate):
        fig = fn(ticks, "foyle")
        assert fig.axes  # built without error
        import matplotlib.pyplot as plt
        plt.close(fig)


def test_import_replay_stays_light() -> None:
    """eval.replay loads chroma/torch lazily — importing it must not."""
    code = ("import sys; import eval.replay; "
            "assert 'chromadb' not in sys.modules, 'chromadb'; "
            "assert 'torch' not in sys.modules, 'torch'; "
            "assert 'anthropic' not in sys.modules, 'anthropic'")
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=config.ROOT, timeout=120)
