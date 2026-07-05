"""Audit-agent CLI.

    python -m audit.run --profile foyle                       # LLM proposal
    python -m audit.run --profile foyle --offline             # heuristic only
    python -m audit.run --drive path/to/folder --profile x    # explicit folder
    AUDIT_MODEL=claude-sonnet-4-6 python -m audit.run ...     # model override

Runs as its own process on purpose: it needs only pandas + openpyxl + spaCy +
anthropic, and must never import chromadb/pyarrow/torch (the Windows native-lib
segfault constraint documented in ingest.py).
"""

from __future__ import annotations

import os

# Same thread-pinning as ingest.py — spaCy pulls in thinc/blis whose OpenMP/BLAS
# runtimes misbehave on Windows unless pinned. Must precede other imports.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
from pathlib import Path

import config
from audit.propose import build_proposal, write_proposal


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from the repo's .env (gitignored) into os.environ.
    Holds ANTHROPIC_API_KEY so the audit agent can run without a shell-level
    env var. Deliberately minimal — no quoting/expansion, no dependency."""
    env_file = config.ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Propose a canonical-schema mapping for a messy drive")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES),
                        help="SME profile (names the proposal artefact + default drive)")
    parser.add_argument("--drive", type=Path, default=None,
                        help="folder of xlsx files (default: the profile's synthetic drive)")
    parser.add_argument("--offline", action="store_true",
                        help="skip the LLM; promote the heuristic baseline to the proposal")
    parser.add_argument("--model", default=None,
                        help="Anthropic model id (default: %(default)s -> AUDIT_MODEL or "
                             f"{config.AUDIT_MODEL_DEFAULT})")
    args = parser.parse_args()

    drive = args.drive or config.MESSY_PROFILES[args.profile]["dir"]
    proposal = build_proposal(drive, args.profile, offline=args.offline, model=args.model)
    out = write_proposal(proposal)

    n_events = sum(1 for f in proposal.files if f.inferred_role == "events")
    print(f"[audit] {proposal.mode} proposal over {len(proposal.files)} sheets "
          f"({n_events} events-role) -> {out}")
    mess = proposal.mess_report
    for kind in ("duplicates", "overlaps", "gaps", "irrelevant"):
        for item in getattr(mess, kind):
            print(f"[audit]   {kind}: {item}")
    print("[audit] Next: review + approve it in the dashboard's Mapping Review tab.")


if __name__ == "__main__":
    main()
