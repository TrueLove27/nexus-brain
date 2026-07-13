"""Shared embedding utilities for memory backends.

nomic-embed-text produces 768-dimensional vectors. When a vector has a different
length, callers should store NULL in embedding_vec and keep the JSONB embedding.
"""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger("nexus.embeddings")

# nomic-embed-text (Ollama) → 768 dims
EMBED_DIM = 768

_embed_fail_logged = False


def embed_text(text: str, embed_model: str, ollama_url: str) -> list[float] | None:
    global _embed_fail_logged
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/embeddings",
            json={"model": embed_model, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        emb = resp.json().get("embedding")
        if not emb:
            return None
        if len(emb) != EMBED_DIM:
            log.warning(
                "embedding dim mismatch: got %d, expected %d (model=%s) — JSONB ok, vector skipped",
                len(emb),
                EMBED_DIM,
                embed_model,
            )
        return emb
    except Exception as exc:
        if not _embed_fail_logged:
            print(f"[nexus] embedding failed ({embed_model}): {exc}")
            _embed_fail_logged = True
            log.warning("embedding failed: %s", exc)
        return None


def embedding_to_pgvector(embedding: list[float]) -> str | None:
    """Format a float list as a pgvector literal. Returns None on dim mismatch."""
    if not embedding or len(embedding) != EMBED_DIM:
        return None
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity for SQLite / fallback ranking (kept for non-pgvector paths)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def rank_by_embedding(
    query_emb: list[float],
    rows: list[tuple],
    content_idx: int = 1,
    embedding_idx: int = 2,
    limit: int = 8,
    min_score: float = 0.3,
) -> list[str]:
    """Rank rows by cosine similarity. Row tuple: (id, content, embedding_json, ...)."""
    scored = []
    for row in rows:
        raw = row[embedding_idx]
        if not raw:
            continue
        emb = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(emb, dict):
            # unexpected shape
            continue
        score = cosine_similarity(query_emb, emb)
        scored.append((score, row[content_idx], row[0]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:limit] if s[0] > min_score]
