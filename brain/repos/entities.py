"""PostgreSQL CRUD for the project/entity knowledge graph."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

ENTITY_TYPES = frozenset(
    {"project", "file", "tool", "concept", "person", "repo", "other"}
)

# Common relation types (open text allowed; these are conventions)
RELATION_TYPES = frozenset(
    {
        "USED_TOOL",
        "FAILED_ON",
        "LEARNED_FROM",
        "PREFERS",
        "TOUCHES",
        "PART_OF",
        "RELATED_TO",
        "MENTIONS",
    }
)


def normalize_entity_name(name: str) -> str:
    """Stable key for UNIQUE(entity_type, normalized_name)."""
    s = (name or "").strip().lower()
    s = re.sub(r"[\s_/\\]+", " ", s)
    s = re.sub(r"[^\w.\- #:@]+", "", s, flags=re.UNICODE)
    return s.strip()[:200] or "unnamed"


def _emb_json(embedding: list[float] | None) -> str | None:
    return json.dumps(embedding) if embedding else None


def _parse_meta(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


class EntitiesRepo:
    def __init__(self, conn_factory, embed_fn=None):
        self._conn = conn_factory
        self._embed = embed_fn

    def upsert(
        self,
        name: str,
        entity_type: str = "other",
        *,
        description: str | None = None,
        metadata: dict | None = None,
        embedding: list[float] | None = None,
        bump_mention: bool = True,
    ) -> dict[str, Any]:
        et = (entity_type or "other").strip().lower()
        if et not in ENTITY_TYPES:
            et = "other"
        norm = normalize_entity_name(name)
        display = (name or "").strip()[:300] or norm
        now = datetime.now(timezone.utc)
        if embedding is None and self._embed and (description or display):
            embedding = self._embed(f"{et}: {display}. {description or ''}".strip())
        meta = metadata or {}
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO kg_entities
                    (entity_type, name, normalized_name, description, embedding,
                     metadata, mention_count, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, %s)
                ON CONFLICT (entity_type, normalized_name) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, kg_entities.name),
                    description = COALESCE(
                        NULLIF(EXCLUDED.description, ''),
                        kg_entities.description
                    ),
                    embedding = COALESCE(EXCLUDED.embedding, kg_entities.embedding),
                    metadata = kg_entities.metadata || EXCLUDED.metadata,
                    mention_count = kg_entities.mention_count + CASE
                        WHEN %s THEN 1 ELSE 0 END,
                    updated_at = EXCLUDED.updated_at
                RETURNING id, entity_type, name, normalized_name, description,
                          metadata, mention_count, created_at, updated_at
                """,
                (
                    et,
                    display,
                    norm,
                    description,
                    _emb_json(embedding),
                    json.dumps(meta),
                    now,
                    now,
                    bump_mention,
                ),
            ).fetchone()
            conn.commit()
        return dict(row) if row else {}

    def get(self, entity_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, entity_type, name, normalized_name, description,
                          metadata, mention_count, created_at, updated_at
                   FROM kg_entities WHERE id = %s""",
                (entity_id,),
            ).fetchone()
        return dict(row) if row else None

    def find(
        self,
        name: str,
        entity_type: str | None = None,
    ) -> dict[str, Any] | None:
        norm = normalize_entity_name(name)
        with self._conn() as conn:
            if entity_type:
                et = entity_type.strip().lower()
                row = conn.execute(
                    """SELECT id, entity_type, name, normalized_name, description,
                              metadata, mention_count, created_at, updated_at
                       FROM kg_entities
                       WHERE entity_type = %s AND normalized_name = %s""",
                    (et, norm),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT id, entity_type, name, normalized_name, description,
                              metadata, mention_count, created_at, updated_at
                       FROM kg_entities
                       WHERE normalized_name = %s
                       ORDER BY mention_count DESC
                       LIMIT 1""",
                    (norm,),
                ).fetchone()
        return dict(row) if row else None

    def search_by_text(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Match entities whose name/description appear in or match query tokens."""
        q = (query or "").strip()
        if not q:
            return []
        tokens = [t for t in re.split(r"\W+", q.lower()) if len(t) > 2][:12]
        with self._conn() as conn:
            # Substring match on name / normalized_name / description
            rows = conn.execute(
                """
                SELECT id, entity_type, name, normalized_name, description,
                       metadata, mention_count, created_at, updated_at
                FROM kg_entities
                WHERE LOWER(name) LIKE %s
                   OR normalized_name LIKE %s
                   OR LOWER(COALESCE(description, '')) LIKE %s
                ORDER BY mention_count DESC, updated_at DESC
                LIMIT %s
                """,
                (f"%{q.lower()[:80]}%", f"%{normalize_entity_name(q)[:80]}%", f"%{q.lower()[:80]}%", limit),
            ).fetchall()
            hits = [dict(r) for r in rows]
            if hits or not tokens:
                return hits[:limit]

            # Token OR match
            scored: dict[int, tuple[int, dict]] = {}
            for tok in tokens:
                trows = conn.execute(
                    """
                    SELECT id, entity_type, name, normalized_name, description,
                           metadata, mention_count, created_at, updated_at
                    FROM kg_entities
                    WHERE normalized_name LIKE %s
                       OR LOWER(name) LIKE %s
                       OR LOWER(COALESCE(description, '')) LIKE %s
                    LIMIT 40
                    """,
                    (f"%{tok}%", f"%{tok}%", f"%{tok}%"),
                ).fetchall()
                for r in trows:
                    d = dict(r)
                    eid = int(d["id"])
                    prev = scored.get(eid)
                    score = (prev[0] if prev else 0) + 1 + int(d.get("mention_count") or 0) // 10
                    scored[eid] = (score, d)
            ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
            return [d for _, d in ranked[:limit]]

    def link(
        self,
        from_id: int,
        to_id: int,
        relation_type: str,
        *,
        weight: float = 1.0,
        evidence: str | None = None,
        task_id: int | None = None,
        accumulate: bool = True,
    ) -> dict[str, Any]:
        if from_id == to_id:
            return {}
        rel = (relation_type or "RELATED_TO").strip().upper()[:64]
        w = max(0.0, float(weight))
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            existing = conn.execute(
                """
                SELECT id, weight FROM kg_relations
                WHERE from_id = %s AND to_id = %s AND relation_type = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (from_id, to_id, rel),
            ).fetchone()
            if existing and accumulate:
                new_w = float(existing["weight"] or 0) + w
                row = conn.execute(
                    """
                    UPDATE kg_relations
                    SET weight = %s,
                        evidence = COALESCE(NULLIF(%s, ''), evidence),
                        task_id = COALESCE(%s, task_id)
                    WHERE id = %s
                    RETURNING id, from_id, to_id, relation_type, weight,
                              evidence, task_id, created_at
                    """,
                    (new_w, evidence, task_id, existing["id"]),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    INSERT INTO kg_relations
                        (from_id, to_id, relation_type, weight, evidence, task_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, from_id, to_id, relation_type, weight,
                              evidence, task_id, created_at
                    """,
                    (from_id, to_id, rel, w, evidence, task_id, now),
                ).fetchone()
            conn.commit()
        return dict(row) if row else {}

    def neighborhood(
        self,
        entity_id: int,
        *,
        hops: int = 1,
        limit: int = 40,
    ) -> dict[str, Any]:
        """1-hop (or multi) neighborhood: entity + edges + neighbor nodes."""
        center = self.get(entity_id)
        if not center:
            return {"entity": None, "relations": [], "neighbors": []}
        hop = max(1, min(int(hops), 2))
        frontier = {int(entity_id)}
        seen_nodes = {int(entity_id)}
        all_rels: list[dict] = []
        neighbors: dict[int, dict] = {}

        with self._conn() as conn:
            for _ in range(hop):
                if not frontier:
                    break
                ids = list(frontier)
                frontier = set()
                rows = conn.execute(
                    """
                    SELECT r.id, r.from_id, r.to_id, r.relation_type, r.weight,
                           r.evidence, r.task_id, r.created_at
                    FROM kg_relations r
                    WHERE r.from_id = ANY(%s) OR r.to_id = ANY(%s)
                    ORDER BY r.weight DESC, r.created_at DESC
                    LIMIT %s
                    """,
                    (ids, ids, limit),
                ).fetchall()
                for r in rows:
                    d = dict(r)
                    all_rels.append(d)
                    for nid in (int(d["from_id"]), int(d["to_id"])):
                        if nid not in seen_nodes:
                            seen_nodes.add(nid)
                            frontier.add(nid)

            if len(seen_nodes) > 1:
                nb_rows = conn.execute(
                    """
                    SELECT id, entity_type, name, normalized_name, description,
                           metadata, mention_count, created_at, updated_at
                    FROM kg_entities
                    WHERE id = ANY(%s) AND id <> %s
                    """,
                    (list(seen_nodes), entity_id),
                ).fetchall()
                for r in nb_rows:
                    d = dict(r)
                    d["metadata"] = _parse_meta(d.get("metadata"))
                    neighbors[int(d["id"])] = d

        center["metadata"] = _parse_meta(center.get("metadata"))
        # Dedupe relations by id
        uniq: dict[int, dict] = {}
        for rel in all_rels:
            uniq[int(rel["id"])] = rel
        rels = sorted(uniq.values(), key=lambda r: float(r.get("weight") or 0), reverse=True)[
            :limit
        ]
        return {
            "entity": center,
            "relations": rels,
            "neighbors": list(neighbors.values())[:limit],
        }

    def stats(self) -> dict[str, int]:
        with self._conn() as conn:
            e = conn.execute("SELECT COUNT(*) AS c FROM kg_entities").fetchone()
            r = conn.execute("SELECT COUNT(*) AS c FROM kg_relations").fetchone()
        return {
            "kg_entities": int(e["c"] if e else 0),
            "kg_relations": int(r["c"] if r else 0),
        }
