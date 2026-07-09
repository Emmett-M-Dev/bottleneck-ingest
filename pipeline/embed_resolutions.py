"""Embed the resolution corpus into its own ChromaDB collection.

    python -m pipeline.embed_resolutions --profile foyle [--reset]

Writes to "sme_resolutions" — deliberately separate from the "sme_ops" event
corpus, which ingest.py resets on every run. The resolution store is the RAG
knowledge base ("how similar problems were fixed") and must survive ingests;
re-run this module only when data/synthetic/resolutions_<profile>.json
changes. Upsert ids are the resolution_ids, so re-running replaces entries
in place rather than duplicating them.

Both profiles share the one collection; entries carry a `profile` metadata
field so retrieval can filter to the active SME (cross-profile entries then
double as distractors in the retrieval eval).
"""

from __future__ import annotations

import os

# Same thread-pinning as pipeline/embed.py — must precede the native libs.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import argparse
import json

import config
from pipeline import embed


def get_resolutions_collection():
    return embed._get_client().get_or_create_collection(config.RESOLUTIONS_COLLECTION)


def reset_resolutions_collection() -> None:
    """Drop ONLY the resolution collection (sme_ops is untouched)."""
    client = embed._get_client()
    try:
        client.delete_collection(config.RESOLUTIONS_COLLECTION)
    except Exception:
        pass  # collection may not exist yet
    client.get_or_create_collection(config.RESOLUTIONS_COLLECTION)


def embed_resolutions(profile: str) -> int:
    """Embed one profile's corpus. Returns the number of entries upserted."""
    entries = json.loads(config.resolutions_path(profile).read_text(encoding="utf-8"))
    if not entries:
        return 0
    model = embed._get_model()
    texts = [f"{e['problem_description']} {e['action_taken']} {e['outcome']}"
             for e in entries]
    embeddings = model.encode(texts, show_progress_bar=False,
                              normalize_embeddings=True).tolist()
    ids = [e["resolution_id"] for e in entries]
    metadatas = [{k: e[k] for k in ("profile", "bottleneck_type", "stage",
                                    "days_to_resolve", "source")}
                 for e in entries]
    get_resolutions_collection().upsert(ids=ids, documents=texts,
                                        embeddings=embeddings, metadatas=metadatas)
    return len(entries)


def query_resolutions(text: str, n_results: int = 3,
                      profile: str | None = None) -> dict:
    """Nearest past resolutions for a query string, optionally per-profile."""
    model = embed._get_model()
    q_emb = model.encode([text], normalize_embeddings=True).tolist()
    where = {"profile": profile} if profile else None
    return get_resolutions_collection().query(query_embeddings=q_emb,
                                              n_results=n_results, where=where)


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed the resolution corpus into sme_resolutions")
    parser.add_argument("--profile", required=True, choices=sorted(config.MESSY_PROFILES))
    parser.add_argument("--reset", action="store_true",
                        help="drop the resolution collection first (both profiles!)")
    args = parser.parse_args()

    if args.reset:
        reset_resolutions_collection()
    n = embed_resolutions(args.profile)
    total = get_resolutions_collection().count()
    print(f"Embedded {n} {args.profile} resolutions into "
          f"'{config.RESOLUTIONS_COLLECTION}' ({total} entries total)")


if __name__ == "__main__":
    main()
