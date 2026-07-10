"""Provider-layer tests: schema-valid replies parse, an invalid reply triggers
exactly one retry, and every failure mode (connection refused, HTTP error,
still-invalid JSON) degrades to None — the pipeline must never crash because
the local LLM is absent. All HTTP is stubbed; no Ollama needed.
"""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest
from pydantic import BaseModel

import config
from pipeline.llm import complete_json


class Finding(BaseModel):
    title: str
    score: float


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ollama_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def test_valid_reply_parses() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return _ollama_reply(json.dumps({"title": "slow stage", "score": 0.7}))

    result = complete_json("find issues", {"stats": 1}, Finding, client=_client(handler))
    assert result == Finding(title="slow stage", score=0.7)
    assert len(calls) == 1
    assert calls[0]["format"] == "json"
    assert calls[0]["stream"] is False


def test_invalid_shape_retries_once_with_error_fed_back() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _ollama_reply(json.dumps({"wrong": "shape"}))
        return _ollama_reply(json.dumps({"title": "fixed", "score": 0.5}))

    result = complete_json("find issues", {}, Finding, client=_client(handler))
    assert result == Finding(title="fixed", score=0.5)
    assert len(calls) == 2
    # The retry prompt quotes the validation failure back to the model.
    assert "failed validation" in calls[1]["messages"][0]["content"]


def test_still_invalid_after_retry_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ollama_reply(json.dumps({"wrong": "shape"}))

    assert complete_json("s", {}, Finding, client=_client(handler)) is None


@pytest.mark.parametrize("handler", [
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused")),  # no Ollama
    lambda req: httpx.Response(500, json={"error": "boom"}),           # HTTP error
    lambda req: httpx.Response(200, json={"unexpected": "keys"}),      # bad envelope
])
def test_transport_failures_return_none(handler) -> None:
    assert complete_json("s", {}, Finding, client=_client(handler)) is None


def test_llm_import_is_light() -> None:
    """The repo rule: no chromadb/torch outside the embedding processes."""
    code = ("import sys, pipeline.llm; "
            "bad = [m for m in ('chromadb', 'torch', 'spacy') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True,
                   cwd=str(config.ROOT), timeout=120)
