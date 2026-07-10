"""Local-LLM provider layer (Ollama) for low-stakes, exploratory calls.

    from pipeline.llm import complete_json
    result = complete_json(system, payload, MySchema)   # -> MySchema | None

The division of labour across the pipeline's three LLM call sites:

    mapping inference (audit/infer.py)  -> Claude   (precision task, Gate 1)
    RAG diagnosis (pipeline/diagnose.py)-> Claude   (precision task, Gate 2)
    anomaly pass (detection/anomaly.py) -> Ollama   (exploratory, this module)

The anomaly pass is advisory — its findings are badged "unverified" in the UI
and never gate anything — so it runs on a free local model. That keeps the
marginal cost of every export at zero and keeps the exploratory payload
entirely on-machine (it is aggregate stats only, but local inference makes the
privacy story unconditional).

Failure mode is silence, not errors: if Ollama isn't running, the model isn't
pulled, or the reply fails schema validation after one retry, the caller gets
None and skips the pass. The pipeline must never break because a local LLM is
absent.

Uses httpx (already a transitive of the anthropic SDK — no new dependency).
Never imports chromadb / torch.
"""

from __future__ import annotations

import json
import os

import httpx
from pydantic import BaseModel, ValidationError

OLLAMA_URL_DEFAULT = "http://localhost:11434"
OLLAMA_MODEL_DEFAULT = "qwen2.5:7b"

_TIMEOUT_SECONDS = 120.0  # local 7B models can be slow on first (cold) load


def _ollama_chat(system: str, user: str, *, url: str, model: str,
                 client: httpx.Client | None = None) -> str:
    """One non-streaming Ollama chat call in JSON mode; returns the raw reply."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": "json",     # constrains decoding to valid JSON
        "stream": False,
        "options": {"temperature": 0},  # deterministic-ish for reproducibility
    }
    if client is None:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as own:
            resp = own.post(f"{url}/api/chat", json=payload)
    else:
        resp = client.post(f"{url}/api/chat", json=payload)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def complete_json(system: str, payload: dict, schema: type[BaseModel], *,
                  model: str | None = None, url: str | None = None,
                  client: httpx.Client | None = None) -> BaseModel | None:
    """Ask the local model for a `schema`-shaped JSON reply; None on any failure.

    The schema's JSON schema is embedded in the system prompt (Ollama's json
    mode guarantees syntax, not shape) and the reply is pydantic-validated.
    One retry on invalid shape — with the validation error quoted back to the
    model — then give up quietly."""
    url = (url or os.environ.get("OLLAMA_URL") or OLLAMA_URL_DEFAULT).rstrip("/")
    model = model or os.environ.get("OLLAMA_MODEL") or OLLAMA_MODEL_DEFAULT

    system_full = (
        f"{system}\n\nReply with a single JSON object matching this JSON "
        f"schema exactly (no extra keys, no prose):\n"
        f"{json.dumps(schema.model_json_schema())}"
    )
    user = json.dumps(payload, ensure_ascii=False)

    last_error: str | None = None
    for _ in range(2):  # first attempt + one retry
        prompt = system_full if last_error is None else (
            f"{system_full}\n\nYour previous reply failed validation with:\n"
            f"{last_error}\nCorrect the JSON."
        )
        try:
            raw = _ollama_chat(prompt, user, url=url, model=model, client=client)
        except (httpx.HTTPError, KeyError, json.JSONDecodeError, OSError):
            return None  # Ollama absent / HTTP error — skip, never crash
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            last_error = str(exc)[:500]
    return None
