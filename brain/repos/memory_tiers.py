"""PostgreSQL repository for multi-tier cognitive memory."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from brain.embeddings import cosine_similarity, rank_by_embedding
from brain.memory_tiers import normalize_source


def _emb_json(embedding: list[float] | None) -> str | None:
    return json.dumps(embedding) if embedding else None


def _parse_emb(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else None
        except Exception:
            return None
    return None


class MemoryTiersRepo:
    def __init__(self, conn_factory, embed_fn=None):
        self._conn = conn_factory
        self._embed = embed_fn

    # ── Episodes ──────────────────────────────────────────────────────────

    def insert_episode(
        self,
        content: str,
        source: str = "task",
        *,
        task_id: int | None = None,
        conversation_id: int | None = None,
        job_id: int | None = None,
        embedding: list[float] | None = None,
        metadata: dict | None = None,
    ) -> int:
        now = datetime.now(timezone.utc)
        source = normalize_source(source)
        if embedding is None and self._embed:
            embedding = self._embed(content)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO memory_episodes
                   (source, task_id, conversation_id, job_id, content, embedding, metadata, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    source,
                    task_id,
                    conversation_id,
                    job_id,
                    content,
                    _emb_json(embedding),
                    json.dumps(metadata or {}),
                    now,
                ),
            ).fetchone()
            conn.commit()
            return int(row["id"]) if row else 0

    def get_unconsolidated(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, source, task_id, conversation_id, job_id, content,
                          embedding, metadata, created_at, consolidated_at
                   FROM memory_episodes
                   WHERE consolidated_at IS NULL
                   ORDER BY created_at ASC
                   LIMIT %s""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_unconsolidated(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_episodes WHERE consolidated_at IS NULL"
            ).fetchone()
        return int(row["n"] if row else 0)

    def mark_consolidated(self, episode_ids: list[int]) -> int:
        if not episode_ids:
            return 0
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """UPDATE memory_episodes
                   SET consolidated_at = %s
                   WHERE id = ANY(%s) AND consolidated_at IS NULL""",
                (now, list(episode_ids)),
            )
            conn.commit()
        return len(episode_ids)

    def search_episodes(
        self,
        query: str,
        limit: int = 4,
        *,
        query_emb: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, source, content, embedding, created_at, metadata
                   FROM memory_episodes
                   ORDER BY created_at DESC
                   LIMIT 100"""
            ).fetchall()
        return self._rank_rows(query, rows, limit, query_emb=query_emb, bump=False)

    def recent_episodes(self, limit: int = 4) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, source, content, embedding, created_at, metadata
                   FROM memory_episodes
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Facts ─────────────────────────────────────────────────────────────

    def insert_fact(
        self,
        content: str,
        *,
        confidence: float = 0.7,
        source_episode_ids: list[int] | None = None,
        embedding: list[float] | None = None,
        category: str = "general",
    ) -> int:
        now = datetime.now(timezone.utc)
        if embedding is None and self._embed:
            embedding = self._embed(content)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO memory_facts
                   (content, embedding, confidence, source_episode_ids, category, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    content,
                    _emb_json(embedding),
                    max(0.0, min(1.0, float(confidence))),
                    json.dumps(source_episode_ids or []),
                    category or "general",
                    now,
                    now,
                ),
            ).fetchone()
            conn.commit()
            return int(row["id"]) if row else 0

    def search_facts(
        self,
        query: str,
        limit: int = 5,
        *,
        query_emb: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, content, embedding, confidence, source_episode_ids,
                          superseded_by, access_count, category, created_at, updated_at
                   FROM memory_facts
                   WHERE superseded_by IS NULL
                   ORDER BY created_at DESC
                   LIMIT 200"""
            ).fetchall()
        hits = self._rank_rows(query, rows, limit, query_emb=query_emb, bump=True, table="facts")
        return hits

    def mark_superseded(self, fact_id: int, superseded_by: int) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """UPDATE memory_facts
                   SET superseded_by = %s, updated_at = %s
                   WHERE id = %s""",
                (superseded_by, now, fact_id),
            )
            conn.commit()

    def bump_fact_access(self, fact_ids: list[int]) -> None:
        if not fact_ids:
            return
        with self._conn() as conn:
            conn.execute(
                """UPDATE memory_facts
                   SET access_count = access_count + 1, updated_at = %s
                   WHERE id = ANY(%s)""",
                (datetime.now(timezone.utc), list(fact_ids)),
            )
            conn.commit()

    # ── Procedures ────────────────────────────────────────────────────────

    def insert_procedure(
        self,
        trigger_text: str,
        content: str,
        *,
        confidence: float = 0.7,
        embedding: list[float] | None = None,
        category: str = "general",
    ) -> int:
        now = datetime.now(timezone.utc)
        blend = f"{trigger_text}\n{content}"
        if embedding is None and self._embed:
            embedding = self._embed(blend)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO memory_procedures
                   (trigger_text, content, embedding, confidence, category, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    trigger_text,
                    content,
                    _emb_json(embedding),
                    max(0.0, min(1.0, float(confidence))),
                    category or "general",
                    now,
                ),
            ).fetchone()
            conn.commit()
            return int(row["id"]) if row else 0

    def search_procedures(
        self,
        query: str,
        limit: int = 3,
        *,
        query_emb: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, trigger_text, content, embedding, confidence,
                          use_count, category, created_at
                   FROM memory_procedures
                   ORDER BY created_at DESC
                   LIMIT 200"""
            ).fetchall()
        # Rank on trigger+content blend stored in embedding; fall back to content field for display
        ranked = self._rank_procedure_rows(query, rows, limit, query_emb=query_emb)
        if ranked:
            ids = [r["id"] for r in ranked]
            with self._conn() as conn:
                conn.execute(
                    """UPDATE memory_procedures
                       SET use_count = use_count + 1
                       WHERE id = ANY(%s)""",
                    (ids,),
                )
                conn.commit()
        return ranked

    def bump_procedure_use(self, procedure_ids: list[int]) -> None:
        if not procedure_ids:
            return
        with self._conn() as conn:
            conn.execute(
                """UPDATE memory_procedures
                   SET use_count = use_count + 1
                   WHERE id = ANY(%s)""",
                (list(procedure_ids),),
            )
            conn.commit()

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            ep = conn.execute("SELECT COUNT(*) AS n FROM memory_episodes").fetchone()
            unc = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_episodes WHERE consolidated_at IS NULL"
            ).fetchone()
            facts = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_facts WHERE superseded_by IS NULL"
            ).fetchone()
            procs = conn.execute("SELECT COUNT(*) AS n FROM memory_procedures").fetchone()
        return {
            "episodes": int(ep["n"] if ep else 0),
            "unconsolidated": int(unc["n"] if unc else 0),
            "facts": int(facts["n"] if facts else 0),
            "procedures": int(procs["n"] if procs else 0),
        }

    # ── Ranking helpers ───────────────────────────────────────────────────

    def _rank_rows(
        self,
        query: str,
        rows: list,
        limit: int,
        *,
        query_emb: list[float] | None = None,
        bump: bool = False,
        table: str = "facts",
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        emb = query_emb
        if emb is None and self._embed:
            emb = self._embed(query)

        dicts = [dict(r) for r in rows]
        if emb:
            tuples = [
                (d["id"], d["content"], json.dumps(d["embedding"]) if d.get("embedding") and not isinstance(d["embedding"], str) else d.get("embedding"))
                for d in dicts
            ]
            ranked_contents = rank_by_embedding(emb, tuples, limit=limit, min_score=0.25)
            by_content = {d["content"]: d for d in dicts}
            hits = [by_content[c] for c in ranked_contents if c in by_content]
            if not hits:
                # Soft fallback: keyword overlap
                hits = self._keyword_rank(query, dicts, limit, text_key="content")
            if bump and hits and table == "facts":
                self.bump_fact_access([h["id"] for h in hits])
            return hits[:limit]

        return self._keyword_rank(query, dicts, limit, text_key="content")

    def _rank_procedure_rows(
        self,
        query: str,
        rows: list,
        limit: int,
        *,
        query_emb: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        emb = query_emb
        if emb is None and self._embed:
            emb = self._embed(query)
        dicts = [dict(r) for r in rows]
        if emb:
            scored: list[tuple[float, dict]] = []
            for d in dicts:
                pe = _parse_emb(d.get("embedding"))
                if not pe:
                    continue
                scored.append((cosine_similarity(emb, pe), d))
            scored.sort(key=lambda x: x[0], reverse=True)
            hits = [d for s, d in scored[:limit] if s > 0.25]
            if hits:
                return hits
        return self._keyword_rank(
            query,
            dicts,
            limit,
            text_key="trigger_text",
            extra_key="content",
        )

    @staticmethod
    def _keyword_rank(
        query: str,
        rows: list[dict],
        limit: int,
        *,
        text_key: str,
        extra_key: str | None = None,
    ) -> list[dict[str, Any]]:
        words = [w.lower() for w in query.split() if len(w) > 3][:8]
        if not words:
            return rows[:limit]
        scored = []
        for r in rows:
            text = (r.get(text_key) or "").lower()
            if extra_key:
                text += " " + (r.get(extra_key) or "").lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:limit]] if scored else rows[:limit]
