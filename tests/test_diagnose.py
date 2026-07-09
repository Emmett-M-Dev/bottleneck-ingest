"""Diagnosis-agent tests: the zero-PII payload guarantee, the request shape
(no temperature, adaptive thinking, schema-constrained output), the actionable
missing-key error, and the offline fallback — all against a stub client, never
the network, and without ever importing chromadb/torch.
"""

from __future__ import annotations

import json
import sys

import pytest

from pipeline.diagnose import (DiagnosisResult, SuggestedFix, diagnose,
                               offline_diagnosis)


def _bn_with_pii() -> dict:
    """A detected-bottleneck dict whose evidence carries every PII shape the
    scrubber must catch before the payload leaves the machine."""
    return {
        "id": "BN001", "type": "delay", "stage": "Booking Confirmed",
        "metric_label": "avg_delay_days", "metric_value": 11.5,
        "affected_count": 3,
        "evidence_excerpts": [
            "Booking B-014 was handled by Una Toner and sat 12 days unconfirmed.",
            "Host family contact: una.toner@example.com, +44 7700 900123, BT48 7NN.",
        ],
    }


def _canned_retrieved() -> list[dict]:
    return [{
        "resolution_id": "RES-FOY-001",
        "source": "resolutions:ops_log_2024",
        "similarity_score": 0.82,
        "summary": "Started host recruitment earlier and kept pre-vetted families.",
        "document": ("Summer bookings sat unconfirmed. Started host recruitment "
                     "in February. Confirmation time dropped to 5 days."),
        "stage": "Booking Confirmed", "bottleneck_type": "delay",
        "days_to_resolve": 14,
    }]


def _canned_result() -> DiagnosisResult:
    return DiagnosisResult(
        diagnosis="Bookings wait on host matching.",
        root_cause="Hosts recruited after offers go out.",
        suggested_fix=SuggestedFix(summary="Recruit hosts earlier",
                                   steps=["Recruit in February."],
                                   rationale="Worked in RES-FOY-001."),
        confidence=0.8,
        retrieved_resolution_ids=["RES-FOY-001"],
    )


class _StubMessages:
    def __init__(self, result: DiagnosisResult) -> None:
        self._result = result
        self.last_kwargs: dict = {}

    def parse(self, **kwargs):
        self.last_kwargs = kwargs

        class _Resp:
            parsed_output = self._result

        return _Resp()


class _StubClient:
    def __init__(self, result: DiagnosisResult) -> None:
        self.messages = _StubMessages(result)


def test_diagnose_payload_has_zero_pii() -> None:
    """Names, emails, phones and postcodes in evidence excerpts must be
    scrubbed before the payload reaches the API — and the retrieval stack
    (chromadb/torch) must never load when `retrieved` is supplied."""
    client = _StubClient(_canned_result())
    diagnose(_bn_with_pii(), "foyle", retrieved=_canned_retrieved(), client=client)

    payload = client.messages.last_kwargs["messages"][0]["content"]
    for leak in ("Una", "Toner", "una.toner", "example.com",
                 "7700", "900123", "BT48"):
        assert leak not in payload, leak
    # the non-PII signal survives scrubbing
    assert "B-014" in payload
    # Retrieval stayed lazy: chromadb never loaded. (torch IS present — spaCy's
    # thinc imports it transitively, same as in audit/ — the segfault triad
    # needs chromadb+pyarrow alongside it, so chromadb is the one to gate.)
    assert "chromadb" not in sys.modules


def test_diagnose_request_shape() -> None:
    client = _StubClient(_canned_result())
    result = diagnose(_bn_with_pii(), "foyle",
                      retrieved=_canned_retrieved(), client=client)

    sent = client.messages.last_kwargs
    assert sent["output_format"] is DiagnosisResult
    assert "temperature" not in sent                      # 400 on Opus 4.8
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["model"] == "claude-opus-4-8"
    assert result.retrieved_resolution_ids == ["RES-FOY-001"]
    # retrieved resolutions ride along in the payload for grounding
    payload = json.loads(sent["messages"][0]["content"])
    assert payload["past_resolutions"][0]["resolution_id"] == "RES-FOY-001"
    assert payload["sme_context"]["domain"] == "international education placement"


def test_missing_api_key_is_actionable(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="--offline"):
        diagnose(_bn_with_pii(), "foyle", retrieved=_canned_retrieved())


def test_offline_diagnosis_round_trips() -> None:
    result = offline_diagnosis(_bn_with_pii(), _canned_retrieved())
    assert DiagnosisResult.model_validate(result.model_dump())
    assert result.retrieved_resolution_ids == ["RES-FOY-001"]
    assert 0.0 <= result.confidence <= 1.0
    assert "Booking Confirmed" in result.diagnosis


def test_offline_diagnosis_without_retrieval() -> None:
    result = offline_diagnosis(_bn_with_pii())
    assert result.retrieved_resolution_ids == []
    assert result.suggested_fix.steps
