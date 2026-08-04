import json
import random

from simulator.compose import TEMPLATES, compose
from simulator.intents import CATALOGUE
from simulator.profiles import profile_config
from simulator.world import day0_from_generator

CFG = profile_config("advisory")


def _case():
    return next(iter(day0_from_generator("advisory").cases.values()))


def test_every_catalogue_intent_has_a_template():
    assert set(TEMPLATES) == set(CATALOGUE)


def test_compose_without_llm_is_deterministic(tmp_path):
    kw = dict(day=3, seq=1, case=_case(), cache_dir=tmp_path, use_llm=False,
              cfg=CFG)
    a = compose(CATALOGUE["client_query"], rng=random.Random(5), **kw)
    b = compose(CATALOGUE["client_query"], rng=random.Random(5), **kw)
    assert (a.subject, a.body) == (b.subject, b.body)


def test_compose_leaves_no_unfilled_slots(tmp_path):
    for intent in CATALOGUE.values():
        m = compose(intent, day=1, seq=0, case=_case(), rng=random.Random(1),
                    cache_dir=tmp_path, use_llm=False, cfg=CFG)
        assert "{" not in m.subject and "{" not in m.body, intent.id


def test_compose_starts_unapplied_with_a_stable_id(tmp_path):
    m = compose(CATALOGUE["payment_made"], day=12, seq=2, case=_case(),
                rng=random.Random(1), cache_dir=tmp_path, use_llm=False,
                cfg=CFG)
    assert m.msg_id == "M012-02"
    assert m.applied is False and m.row_ref is None


def test_cached_slots_are_reused_instead_of_regenerated(tmp_path):
    case = _case()
    (tmp_path / "0-4-M004-00.json").write_text(
        json.dumps({"detail": "CACHED DETAIL"}), encoding="utf-8")
    m = compose(CATALOGUE["client_query"], day=4, seq=0, case=case,
                rng=random.Random(1), cache_dir=tmp_path, use_llm=True,
                seed=0, cfg=CFG)
    assert "CACHED DETAIL" in m.body


def test_compose_reads_the_fallback_details_from_cfg(tmp_path):
    """F3(b): compose.py must never name a business type itself -- the
    deterministic fallback detail pool comes from the profile block, not a
    module-level constant. Every filled 'detail' slot must trace back to
    cfg["fallback_details"]."""
    case = _case()
    for seed in range(20):
        m = compose(CATALOGUE["client_query"], day=1, seq=0, case=case,
                    rng=random.Random(seed), cache_dir=tmp_path,
                    use_llm=False, cfg=CFG)
        assert any(d in m.body for d in CFG["fallback_details"]), m.body


def test_compose_requires_cfg_and_uses_a_different_profiles_details(tmp_path):
    """A profile with a different fallback_details pool must produce
    messages drawn from ITS pool, not advisory's -- proving compose.py
    reads cfg rather than falling back to hardcoded advisory vocabulary."""
    other_cfg = dict(CFG)
    other_cfg["fallback_details"] = ["a completely distinct phrase"]
    case = _case()
    m = compose(CATALOGUE["client_query"], day=1, seq=0, case=case,
                rng=random.Random(1), cache_dir=tmp_path, use_llm=False,
                cfg=other_cfg)
    assert "a completely distinct phrase" in m.body
