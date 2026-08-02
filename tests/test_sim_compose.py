import json
import random

from simulator.compose import TEMPLATES, compose
from simulator.intents import CATALOGUE
from simulator.world import day0_from_generator


def _case():
    return next(iter(day0_from_generator("advisory").cases.values()))


def test_every_catalogue_intent_has_a_template():
    assert set(TEMPLATES) == set(CATALOGUE)


def test_compose_without_llm_is_deterministic(tmp_path):
    kw = dict(day=3, seq=1, case=_case(), cache_dir=tmp_path, use_llm=False)
    a = compose(CATALOGUE["client_query"], rng=random.Random(5), **kw)
    b = compose(CATALOGUE["client_query"], rng=random.Random(5), **kw)
    assert (a.subject, a.body) == (b.subject, b.body)


def test_compose_leaves_no_unfilled_slots(tmp_path):
    for intent in CATALOGUE.values():
        m = compose(intent, day=1, seq=0, case=_case(), rng=random.Random(1),
                    cache_dir=tmp_path, use_llm=False)
        assert "{" not in m.subject and "{" not in m.body, intent.id


def test_compose_starts_unapplied_with_a_stable_id(tmp_path):
    m = compose(CATALOGUE["payment_made"], day=12, seq=2, case=_case(),
                rng=random.Random(1), cache_dir=tmp_path, use_llm=False)
    assert m.msg_id == "M012-02"
    assert m.applied is False and m.row_ref is None


def test_cached_slots_are_reused_instead_of_regenerated(tmp_path):
    case = _case()
    (tmp_path / "0-4-M004-00.json").write_text(
        json.dumps({"detail": "CACHED DETAIL"}), encoding="utf-8")
    m = compose(CATALOGUE["client_query"], day=4, seq=0, case=case,
                rng=random.Random(1), cache_dir=tmp_path, use_llm=True,
                seed=0)
    assert "CACHED DETAIL" in m.body
