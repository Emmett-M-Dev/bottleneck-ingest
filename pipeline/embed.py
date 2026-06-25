"""Local embeddings (all-MiniLM-L6-v2) -> persistent ChromaDB. No API calls.

Upserts chunks into collection "sme_ops" with source-tracing metadata. IDs are
deterministic ("{record_id}#c{chunk_index}") so re-running ingestion replaces
chunks in place rather than duplicating them.
"""

from __future__ import annotations

import os

# Must be set BEFORE the native libs (chromadb/hnswlib, torch, spaCy's blis) load.
# torch ships Intel OpenMP (libiomp5md); when hnswlib/blis then run their parallel
# work, the OpenMP/BLAS runtimes collide and segfault on Windows at scale. Pinning
# every threading backend to one thread + allowing the duplicate runtime avoids it.
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
           "BLIS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import chromadb

from config import CHROMA_COLLECTION, CHROMA_PATH, EMBEDDING_MODEL

_model = None
_client = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        import torch
        torch.set_num_threads(1)
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def _get_client():
    global _client
    if _client is None:
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return _client


def get_collection():
    return _get_client().get_or_create_collection(CHROMA_COLLECTION)


def reset_collection() -> None:
    """Drop the collection so a run's corpus contains only the current source.

    Each ingest run fully rebuilds records.jsonl + the event log, so the vector store
    must match — otherwise chunks from earlier `--source` runs (e.g. the old single-sheet
    model) linger and pollute RAG retrieval."""
    client = _get_client()
    try:
        client.delete_collection(CHROMA_COLLECTION)
    except Exception:
        pass  # collection may not exist yet
    client.get_or_create_collection(CHROMA_COLLECTION)


def embed_chunks(chunks: list[dict]) -> int:
    """Embed and upsert chunks. Returns the number embedded."""
    if not chunks:
        return 0
    model = _get_model()
    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True).tolist()
    ids = [f"{c['metadata']['record_id']}#c{c['metadata']['chunk_index']}" for c in chunks]
    metadatas = [c["metadata"] for c in chunks]
    get_collection().upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
    return len(chunks)


def query(text: str, n_results: int = 5) -> dict:
    """Embed a query string and return the nearest chunks from the collection."""
    model = _get_model()
    q_emb = model.encode([text], normalize_embeddings=True).tolist()
    return get_collection().query(query_embeddings=q_emb, n_results=n_results)
