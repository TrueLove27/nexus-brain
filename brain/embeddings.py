"""Shared embedding utilities for memory backends."""

from __future__ import annotations

import json

import requests


def embed_text(text: str, embed_model: str, ollama_url: str) -> list[float] | None:
    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/embeddings",
            json={"model": embed_model, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("embedding")
    except Exception:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
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
        score = cosine_similarity(query_emb, emb)
        scored.append((score, row[content_idx], row[0]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:limit] if s[0] > min_score]
