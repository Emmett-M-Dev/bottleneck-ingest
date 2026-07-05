"""The mapping-inference call — the only module in the repo touching the
anthropic SDK.

One request per drive: all sheets go in a single payload so the model can see
cross-file duplicates and overlaps (per-sheet calls could never write the mess
report). Output is schema-constrained via `messages.parse` -> `LLMProposal`,
so a syntactically invalid mapping cannot come back.

No `temperature` is sent — sampling parameters are removed on Opus 4.8 and
return a 400. Determinism comes from the fixed prompt + closed field
vocabulary + schema constraint; run-to-run variance is measured in the eval
instead of suppressed.

Model: `claude-opus-4-8` by default; override with AUDIT_MODEL=claude-sonnet-4-6
for the cost/quality sweep. Auth via ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import os

import anthropic

import config
from audit.schemas import LLMProposal

DEFAULT_MODEL = os.environ.get("AUDIT_MODEL", config.AUDIT_MODEL_DEFAULT)

SYSTEM_PROMPT = """\
You are a data-mapping auditor for a small-business operations pipeline.

You receive a JSON scan of one shared-drive folder: for each spreadsheet, its
filename, sheet name, row count, column headers, and a few sample rows.
Sample cells are anonymised: person-name-shaped values appear as [MASKED_n]
and other identifiers as [PERSON_n]/[EMAIL_n]/etc. A column full of masked
values usually holds people's names.

The pipeline's canonical event schema has exactly five fields:
  case_id    - the identifier that ties rows to one case/booking/job
  activity   - the workflow stage or step label
  timestamp  - when that step happened
  actor      - the staff member who handled it
  status     - freetext status/outcome
A file whose columns map onto these (one row per event) has role "events".
Other roles: "reference" (operational context, no event structure),
"notes" (freetext logs), "ignore" (irrelevant to operations).

For every sheet, return:
- inferred_role and role_confidence (0-1)
- include: whether the pipeline should ingest it. Recommend include=false for
  stale duplicates or superseded copies even when their role is "events".
- columns: for EVERY header, the canonical_field it maps to (or null if none),
  with a confidence per column. Headers are often renamed between seasonal
  copies of the same sheet - map by meaning, not by exact name.
- issues: anything wrong with this specific file.

Also return a drive-level mess_report:
- duplicates: files whose rows duplicate another file's
  (compare case-id values and dates across files)
- overlaps: files covering overlapping periods or cases
- gaps: periods or areas no file covers (look at the date ranges)
- irrelevant: files that don't belong to the operational workflow

Be conservative with confidence: renamed or ambiguous headers deserve lower
confidence so the human reviewer looks at them.
"""


def propose_with_llm(scan: dict, model: str | None = None,
                     client: anthropic.Anthropic | None = None) -> LLMProposal:
    """One structured-output call over the scanned drive. `client` is
    injectable so tests stub it — pytest never hits the network."""
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — the audit agent needs it to call "
            "Claude. Set it, or run with --offline for the heuristic-only "
            "proposal."
        )
    client = client or anthropic.Anthropic()
    model = model or DEFAULT_MODEL
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(scan, ensure_ascii=False)}],
            output_format=LLMProposal,
        )
    except anthropic.AuthenticationError as exc:
        raise RuntimeError(
            "Anthropic API auth failed — set ANTHROPIC_API_KEY, or run with "
            "--offline for the heuristic-only proposal."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError(
            "Could not reach the Anthropic API — check the network, or run "
            "with --offline for the heuristic-only proposal."
        ) from exc
    except anthropic.APIStatusError as exc:
        raise RuntimeError(
            f"Anthropic API error {exc.status_code} on model {model}: {exc.message}"
        ) from exc
    return response.parsed_output
