"""Chroma vector store access.

One client per process. The lock covers both index rebuilds and queries so a
request can never read the collection while another thread is recreating it.
"""

import hashlib
import logging
import threading

import chromadb

from config import settings
from services.errors import VectorStoreError

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(settings.CHROMA_PATH))
    return _client


def build_index(chunks):
    """Re-embed the chunks only if they changed since the last build."""
    fingerprint = hashlib.sha256(
        "\x00".join(f"{c['source']}\x01{c['text']}" for c in chunks).encode("utf-8")
    ).hexdigest()
    name = settings.COLLECTION_NAME
    try:
        with _lock:
            client = _get_client()
            collection = client.get_or_create_collection(
                name, metadata={"hnsw:space": "cosine", "fingerprint": ""}
            )
            if collection.metadata.get("fingerprint") == fingerprint and collection.count() == len(chunks):
                return
            logger.info("Knowledge base changed; rebuilding index (%d chunks)", len(chunks))
            client.delete_collection(name)
            collection = client.create_collection(
                name, metadata={"hnsw:space": "cosine", "fingerprint": fingerprint}
            )
            collection.add(
                ids=[f"chunk-{i}" for i in range(len(chunks))],
                documents=[c["text"] for c in chunks],
                metadatas=[{"source": c["source"]} for c in chunks],
            )
    except Exception as exc:
        logger.exception("Failed to build the vector index")
        raise VectorStoreError() from exc


def retrieve(question, top_k=None, min_score=None):
    """Return up to top_k chunks whose cosine similarity to the question is >= min_score."""
    top_k = settings.TOP_K if top_k is None else top_k
    min_score = settings.MIN_SCORE if min_score is None else min_score
    try:
        with _lock:
            collection = _get_client().get_collection(settings.COLLECTION_NAME)
            count = collection.count()
            if count == 0:
                return []
            results = collection.query(query_texts=[question], n_results=min(top_k, count))
    except Exception as exc:
        logger.exception("Vector search failed")
        raise VectorStoreError() from exc
    retrieved = []
    for text, meta, distance in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        if 1 - distance >= min_score:  # cosine distance -> similarity
            retrieved.append({"source": meta["source"], "text": text})
    return retrieved
