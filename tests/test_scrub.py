"""Scrub tests: seeded PII must not appear in any output; dates + numbers survive."""

from __future__ import annotations

from scrub.anonymise import reset_actor_registry, scrub_actor, scrub_text, scrub_dict

SEED_NAME = "Sarah Jones"
SEED_ORG = "Riverside Academy"
SEED_EMAIL = "sarah.jones@riverside-academy.org"
SEED_PHONE = "07700 900123"
SEED_POSTCODE = "BT48 7PT"

SAMPLE = (
    f"{SEED_NAME} at {SEED_ORG} chased about a booking on 2026-01-12. "
    f"Email {SEED_EMAIL}, phone {SEED_PHONE}, postcode {SEED_POSTCODE}. "
    f"It took 8 to 12 days and cost 1500 pounds."
)


def test_seeded_pii_absent_from_text_output() -> None:
    scrubbed, replacements = scrub_text(SAMPLE)
    for pii in (SEED_NAME, SEED_ORG, SEED_EMAIL, SEED_PHONE, SEED_POSTCODE):
        assert pii not in scrubbed, f"leaked: {pii}"
    assert replacements  # something was replaced


def test_dates_and_numbers_pass_through() -> None:
    scrubbed, _ = scrub_text(SAMPLE)
    assert "2026-01-12" in scrubbed
    assert "8 to 12 days" in scrubbed
    assert "1500" in scrubbed


def test_email_phone_postcode_regex_scrubbed_even_without_ner() -> None:
    text = f"contact {SEED_EMAIL} or {SEED_PHONE}, area {SEED_POSTCODE}"
    scrubbed, reps = scrub_text(text)
    types = {r["type"] for r in reps}
    assert {"EMAIL", "PHONE", "POSTCODE"} <= types
    for pii in (SEED_EMAIL, SEED_PHONE, SEED_POSTCODE):
        assert pii not in scrubbed


def test_stable_placeholder_same_original_same_token() -> None:
    text = f"{SEED_NAME} spoke. Later {SEED_NAME} called again."
    scrubbed, _ = scrub_text(text)
    assert SEED_NAME not in scrubbed
    # Same name -> same placeholder, used twice.
    first = scrubbed.split("spoke")[0].strip()
    assert scrubbed.count(first) == 2


def test_actor_field_masked_even_when_ner_misses_bare_name() -> None:
    # Regression: context-free NER silently missed some bare two-token names
    # (e.g. "Priya Patel"), leaking them into actor + record text. The actor slot
    # must be masked wholesale regardless of whether NER fires.
    reset_actor_registry()
    for name in ("Priya Patel", "Sarah Jones", "John Smith"):
        masked, reps = scrub_actor(name)
        assert name not in masked, f"leaked actor: {name}"
        assert masked.startswith("[") and masked.endswith("]")
        assert reps


def test_actor_placeholder_stable_per_person_across_calls() -> None:
    reset_actor_registry()
    a1, _ = scrub_actor("Priya Patel")
    b1, _ = scrub_actor("Sarah Jones")
    a2, _ = scrub_actor("Priya Patel")
    assert a1 == a2          # same person -> same token across rows
    assert a1 != b1          # distinct people -> distinct tokens


def test_actor_passthrough_for_empty_and_non_string() -> None:
    reset_actor_registry()
    assert scrub_actor(None) == (None, [])
    assert scrub_actor("") == ("", [])
    assert scrub_actor("   ")[0].strip() == ""


def test_scrub_dict_scrubs_string_values_and_keeps_others() -> None:
    record = {
        "Booking Ref": "BR-0001",
        "Handled By": SEED_NAME,
        "Note": f"call {SEED_PHONE}",
        "Count": 5,
        "Missing": None,
    }
    scrubbed, reps = scrub_dict(record)
    assert SEED_NAME not in scrubbed["Handled By"]
    assert SEED_PHONE not in scrubbed["Note"]
    assert scrubbed["Count"] == 5
    assert scrubbed["Missing"] is None
    assert scrubbed["Booking Ref"] == "BR-0001"
