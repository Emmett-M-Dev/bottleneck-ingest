"""Turn an intent into an email.

Template skeletons carry the structure; the LLM fills SLOT VALUES ONLY (names,
counts, figures, a phrasing variant). It never decides which case, which stage,
or whether anything breached — that stays deterministic so ground truth stays
computable and the circularity guard holds.

Fills are cached per (seed, day, msg_id), so a replayed run costs nothing and
runs offline. No key, or any API failure, falls back to deterministic values
and the message still sends.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

TEMPLATES: dict[str, dict[str, str]] = {
    "new_enquiry": {
        "subject": "Enquiry - {detail}",
        "body": ("Hi,\n\nWe're looking for support on {detail}. "
                 "Roughly {figure} to spend. Can you take this on?\n\n{sender}"),
    },
    "progress_update": {
        "subject": "Re: {case_id} - update",
        "body": ("Hi,\n\nJust confirming {detail} on {case_id}. "
                 "Happy for you to move it on.\n\n{sender}"),
    },
    "client_query": {
        "subject": "Question on {case_id}",
        "body": ("Hi,\n\nQuick one - {detail}. Can you come back to me?"
                 "\n\n{sender}"),
    },
    "payment_made": {
        "subject": "{case_id} - payment sent",
        "body": ("Hi,\n\nPayment of {figure} went out today for {case_id}. "
                 "{detail}\n\n{sender}"),
    },
    "scope_change": {
        "subject": "{case_id} - change of scope",
        "body": ("Hi,\n\nWe need to revisit part of this - {detail}. "
                 "Sorry for the go-around.\n\n{sender}"),
    },
}

_FALLBACK_DETAIL = [
    "the March intake", "the reporting pack", "next quarter's phasing",
    "the site visit dates", "the draft findings", "the onboarding pack",
]
_FALLBACK_SENDER = ["R. Hughes", "M. Doherty", "S. Cassidy", "P. Neill",
                    "A. Lynch", "T. Bradley"]


@dataclass
class Message:
    msg_id: str
    day: int
    sent_at: str
    sender: str
    subject: str
    body: str
    intent: str
    case_id: str | None
    applied: bool = False
    row_ref: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _fallback_slots(case, rng: random.Random) -> dict[str, str]:
    value = getattr(case, "value", 0) or 0
    return {
        "detail": rng.choice(_FALLBACK_DETAIL),
        "sender": rng.choice(_FALLBACK_SENDER),
        "figure": f"£{int(value):,}" if value else "£8,000",
    }


def _cache_path(cache_dir: Path, seed: int, day: int, msg_id: str) -> Path:
    return Path(cache_dir) / f"{seed}-{day}-{msg_id}.json"


def _llm_slots(intent_id: str, case, model: str) -> dict[str, str] | None:
    """Ask Claude for slot values. Returns None on any failure — the caller
    falls back, so a missing key or a flaky network degrades the prose, never
    the run."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = (
            "You are writing ONE short, plain business email from a client to "
            "a small consultancy. Reply with JSON only, keys exactly: "
            "detail, sender, figure. 'detail' is a short noun phrase (max 8 "
            "words) about the work. 'sender' is a plausible name. 'figure' is "
            f"a money amount with a pound sign. The email intent is "
            f"'{intent_id}'. Keep it basic and unremarkable."
        )
        resp = client.messages.create(
            model=model, max_tokens=200,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in resp.content if b.type == "text")
        slots = json.loads(text[text.index("{"):text.rindex("}") + 1])
        return {k: str(slots[k]) for k in ("detail", "sender", "figure")}
    except Exception:
        return None


def compose(intent, *, day: int, seq: int, case, rng: random.Random,
            cache_dir, use_llm: bool, seed: int = 0,
            model: str | None = None, start_date=None) -> Message:
    import config

    msg_id = f"M{day:03d}-{seq:02d}"
    cache = _cache_path(cache_dir, seed, day, msg_id)

    slots = _fallback_slots(case, rng)
    if cache.exists():
        slots.update(json.loads(cache.read_text(encoding="utf-8")))
    elif use_llm:
        got = _llm_slots(intent.id, case,
                         model or config.DIAGNOSE_MODEL_DEFAULT)
        if got:
            slots.update(got)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(got), encoding="utf-8")

    slots["case_id"] = getattr(case, "cid", "") if case else ""
    tpl = TEMPLATES[intent.id]
    sent = (start_date + timedelta(days=day)) if start_date else None

    return Message(
        msg_id=msg_id, day=day,
        sent_at=sent.isoformat() if sent else "",
        sender=slots["sender"],
        subject=tpl["subject"].format(**slots),
        body=tpl["body"].format(**slots),
        intent=intent.id,
        case_id=getattr(case, "cid", None) if case else None,
    )
