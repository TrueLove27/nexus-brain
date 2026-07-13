"""PostgreSQL-backed brain memory — durable historical record for the agent."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from brain.embeddings import (
    cosine_similarity,
    embed_text,
    embedding_to_pgvector,
    rank_by_embedding,
)
from brain.memory_tiers import (
    format_episode_line,
    format_fact_line,
    format_procedure_line,
    memory_tiers_config,
    normalize_source,
)
from brain.repos.conversations import ConversationRepo
from brain.repos.entities import EntitiesRepo
from brain.repos.job_queue import JobQueueRepo
from brain.repos.job_traces import JobTraceRepo
from brain.repos.memory_tiers import MemoryTiersRepo
from brain.repos.messages import MessageRepo
from brain.repos.preferences import PreferenceRepo
from brain.repos.tasks import TaskRepo
from brain.repos.tool_calls import ToolCallRepo
from brain.repos.tool_effects import ToolEffectRepo
from db.migrate import run_migrations
from db.pgvector_setup import ensure_pgvector, pgvector_available

log = logging.getLogger("nexus.postgres_memory")


class PostgresMemory:
    def __init__(
        self,
        dsn: str,
        data_dir: Path,
        embed_model: str,
        ollama_url: str,
        max_context_memories: int = 8,
        brain_cfg: dict | None = None,
    ):
        self.dsn = dsn
        self.data_dir = data_dir
        self.embed_model = embed_model
        self.ollama_url = ollama_url.rstrip("/")
        self.tiers_cfg = memory_tiers_config(brain_cfg)
        if brain_cfg and brain_cfg.get("max_context_memories") is not None:
            max_context_memories = brain_cfg["max_context_memories"]
        self.max_context_memories = max(1, int(max_context_memories or 8))
        self.storage_type = "postgres"
        self.db_path = data_dir / "postgres.marker"
        self._pgvector = False
        data_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.conversations = ConversationRepo(self._conn)
        self.messages = MessageRepo(self._conn)
        self.tasks = TaskRepo(self._conn)
        self.tool_calls = ToolCallRepo(self._conn)
        self.tool_effects = ToolEffectRepo(self._conn)
        self.preferences = PreferenceRepo(self._conn)
        self.job_queue = JobQueueRepo(self._conn)
        self.job_traces = JobTraceRepo(self._conn)
        self.memory_tiers = MemoryTiersRepo(self._conn, embed_fn=self._embed)
        self.entities = EntitiesRepo(self._conn, embed_fn=self._embed)
        self._conversation_id: int | None = None
        self._bind_effect_ledger()

    def _bind_effect_ledger(self) -> None:
        """Wire idempotent tool-call effect ledger hooks for reclaim resumes."""
        from core.effect_ledger import set_ledger_hooks

        effects = self.tool_effects
        traces = self.job_traces

        def _lookup(job_id: int, effect_key: str):
            return effects.get(job_id, effect_key)

        def _record(
            job_id: int,
            effect_key: str,
            *,
            action: str,
            args: dict,
            result: str,
            iteration: int | None = None,
            fence_token: int | None = None,
            runner_id: str | None = None,
        ):
            return effects.record_applied(
                job_id,
                effect_key,
                action=action,
                args=args,
                result=result,
                iteration=iteration,
                fence_token=fence_token,
                runner_id=runner_id,
            )

        def _seed(job_id: int, steps: list) -> int:
            return effects.seed_from_steps(job_id, steps)

        def _on_skip(meta: dict) -> None:
            try:
                effects.mark_skipped(int(meta["job_id"]), str(meta["effect_key"]))
            except Exception:
                pass
            try:
                traces.record(
                    int(meta["job_id"]),
                    "effect_skipped",
                    attempt=1,
                    runner_id=meta.get("runner_id"),
                    payload={
                        "effect_key": meta.get("effect_key"),
                        "action": meta.get("action"),
                        "iteration": meta.get("iteration"),
                        "fence_token": meta.get("fence_token"),
                        "result_chars": meta.get("result_chars"),
                    },
                )
            except Exception:
                pass

        set_ledger_hooks(lookup=_lookup, record=_record, seed=_seed, on_skip=_on_skip)

    def _conn(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _init_db(self) -> None:
        run_migrations(self.dsn)
        try:
            with self._conn() as conn:
                self._pgvector = ensure_pgvector(conn)
                if not self._pgvector:
                    self._pgvector = pgvector_available(conn)
        except Exception as exc:
            log.info("pgvector setup skipped: %s", exc)
            self._pgvector = False

    def ensure_conversation(self, session_key: str = "default") -> int:
        self._conversation_id = self.conversations.get_or_create(session_key)
        return self._conversation_id

    @property
    def conversation_id(self) -> int | None:
        return self._conversation_id

    def _embed(self, text: str) -> list[float] | None:
        return embed_text(text, self.embed_model, self.ollama_url)

    def remember(self, content: str, category: str = "general", metadata: dict | None = None) -> int:
        """Dual-write: legacy `memories` bag (+ pgvector) and episodic tier."""
        embedding = self._embed(content)
        now = datetime.now(timezone.utc)
        meta = metadata or {}
        vec = embedding_to_pgvector(embedding) if embedding else None
        with self._conn() as conn:
            if self._pgvector and vec:
                row = conn.execute(
                    """INSERT INTO memories (content, category, embedding, embedding_vec, metadata, created_at)
                       VALUES (%s, %s, %s, %s::vector, %s, %s) RETURNING id""",
                    (
                        content,
                        category,
                        json.dumps(embedding) if embedding else None,
                        vec,
                        json.dumps(meta),
                        now,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    """INSERT INTO memories (content, category, embedding, metadata, created_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (
                        content,
                        category,
                        json.dumps(embedding) if embedding else None,
                        json.dumps(meta),
                        now,
                    ),
                ).fetchone()
            conn.commit()
            mid = row["id"] if row else 0
        try:
            source = normalize_source(str(meta.get("source") or (
                "teach" if category == "preference" else
                "tool" if category == "tool" else
                "task" if category in ("learning", "task") else "chat"
            )))
            self.record_episode(
                content,
                source,
                task_id=meta.get("task_id"),
                conversation_id=meta.get("conversation_id") or self._conversation_id,
                job_id=meta.get("job_id"),
                embedding=embedding,
                metadata={**meta, "legacy_memory_id": mid, "category": category},
            )
        except Exception as exc:
            log.debug("episode dual-write skipped: %s", exc)
        return mid

    def record_episode(
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
        eid = self.memory_tiers.insert_episode(
            content,
            source,
            task_id=task_id,
            conversation_id=conversation_id if conversation_id is not None else self._conversation_id,
            job_id=job_id,
            embedding=embedding,
            metadata=metadata,
        )
        if eid and self._pgvector:
            emb = embedding if embedding is not None else self._embed(content)
            self._bump_row_vector("memory_episodes", eid, emb, text_cols=("content",))
        return eid

    def get_unconsolidated_episodes(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.memory_tiers.get_unconsolidated(limit=limit)

    def count_unconsolidated_episodes(self) -> int:
        return self.memory_tiers.count_unconsolidated()

    def mark_episodes_consolidated(self, episode_ids: list[int]) -> int:
        return self.memory_tiers.mark_consolidated(episode_ids)

    def insert_fact(
        self,
        content: str,
        *,
        confidence: float = 0.7,
        source_episode_ids: list[int] | None = None,
        category: str = "general",
    ) -> int:
        fid = self.memory_tiers.insert_fact(
            content,
            confidence=confidence,
            source_episode_ids=source_episode_ids,
            category=category,
        )
        if fid and self._pgvector:
            self._bump_row_vector("memory_facts", fid, self._embed(content), text_cols=("content",))
        return fid

    def insert_procedure(
        self,
        trigger_text: str,
        content: str,
        *,
        confidence: float = 0.7,
        category: str = "general",
    ) -> int:
        pid = self.memory_tiers.insert_procedure(
            trigger_text,
            content,
            confidence=confidence,
            category=category,
        )
        if pid and self._pgvector:
            self._bump_row_vector(
                "memory_procedures",
                pid,
                self._embed(f"{trigger_text}\n{content}"),
                text_cols=("trigger_text", "content"),
            )
        return pid

    def search_facts(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Hybrid facts search when pgvector available; else JSONB 200-row rank."""
        if self._pgvector:
            try:
                hits = self._hybrid_fact_dicts(query, limit)
                if hits:
                    return hits
            except Exception as exc:
                log.warning("hybrid facts search failed: %s", exc)
        return self.memory_tiers.search_facts(query, limit, query_emb=self._embed(query))

    def search_procedures(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        if self._pgvector:
            try:
                hits = self._hybrid_procedure_dicts(query, limit)
                if hits:
                    return hits
            except Exception as exc:
                log.warning("hybrid procedures search failed: %s", exc)
        return self.memory_tiers.search_procedures(query, limit, query_emb=self._embed(query))

    def search_episodes(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        return self.memory_tiers.search_episodes(query, limit, query_emb=self._embed(query))

    def recent_episodes(self, limit: int = 4) -> list[dict[str, Any]]:
        return self.memory_tiers.recent_episodes(limit=limit)

    def _bump_row_vector(
        self,
        table: str,
        row_id: int,
        embedding: list[float] | None,
        *,
        text_cols: tuple[str, ...],
    ) -> None:
        vec = embedding_to_pgvector(embedding) if embedding else None
        if not vec:
            return
        try:
            with self._conn() as conn:
                conn.execute(
                    f"UPDATE {table} SET embedding_vec = %s::vector WHERE id = %s",
                    (vec, row_id),
                )
                if "content_tsv" in self._columns(table) and text_cols:
                    if len(text_cols) == 1:
                        conn.execute(
                            f"""UPDATE {table}
                               SET content_tsv = to_tsvector('english', coalesce({text_cols[0]}::text, ''))
                               WHERE id = %s""",
                            (row_id,),
                        )
                    else:
                        joined = " || ' ' || ".join(
                            f"coalesce({c}::text, '')" for c in text_cols
                        )
                        conn.execute(
                            f"""UPDATE {table}
                               SET content_tsv = to_tsvector('english', {joined})
                               WHERE id = %s""",
                            (row_id,),
                        )
                conn.commit()
        except Exception as exc:
            log.debug("vector bump skipped %s#%s: %s", table, row_id, exc)

    def _hybrid_fact_dicts(self, query: str, limit: int) -> list[dict[str, Any]]:
        query_emb = self._embed(query)
        vec = embedding_to_pgvector(query_emb) if query_emb else None
        if not vec:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, content, embedding, confidence, source_episode_ids,
                       superseded_by, access_count, category, created_at, updated_at
                FROM memory_facts
                WHERE superseded_by IS NULL AND embedding_vec IS NOT NULL
                ORDER BY
                  (0.50 * (1.0 - (embedding_vec <=> %s::vector))
                   + 0.25 * COALESCE(ts_rank(content_tsv, plainto_tsquery('english', %s)), 0)
                   + 0.15 * COALESCE(confidence, 0.5)
                   + 0.10 * LN(1 + COALESCE(access_count, 0))
                  ) DESC
                LIMIT %s
                """,
                (vec, query, limit),
            ).fetchall()
            hits = [dict(r) for r in rows]
            if hits:
                self.memory_tiers.bump_fact_access([h["id"] for h in hits])
            return hits

    def _hybrid_procedure_dicts(self, query: str, limit: int) -> list[dict[str, Any]]:
        query_emb = self._embed(query)
        vec = embedding_to_pgvector(query_emb) if query_emb else None
        if not vec:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, trigger_text, content, embedding, confidence,
                       use_count, category, created_at
                FROM memory_procedures
                WHERE embedding_vec IS NOT NULL
                ORDER BY
                  (0.55 * (1.0 - (embedding_vec <=> %s::vector))
                   + 0.25 * COALESCE(ts_rank(content_tsv, plainto_tsquery('english', %s)), 0)
                   + 0.20 * LN(1 + COALESCE(use_count, 0))
                  ) DESC
                LIMIT %s
                """,
                (vec, query, limit),
            ).fetchall()
            hits = [dict(r) for r in rows]
            if hits:
                self.memory_tiers.bump_procedure_use([h["id"] for h in hits])
            return hits

    def memory_tier_stats(self) -> dict[str, Any]:
        stats = self.memory_tiers.stats()
        stats["enabled"] = bool(self.tiers_cfg.get("enabled", True))
        stats["consolidate_every_n_episodes"] = int(
            self.tiers_cfg.get("consolidate_every_n_episodes", 5) or 5
        )
        stats["pgvector"] = bool(self._pgvector)
        return stats

    def knowledge_graph_stats(self) -> dict[str, int]:
        try:
            return self.entities.stats()
        except Exception:
            return {"kg_entities": 0, "kg_relations": 0}

    def knowledge_graph_context(self, goal: str) -> str:
        try:
            from brain.knowledge_graph import KnowledgeGraph
            return KnowledgeGraph(self).context_for_goal(goal)
        except Exception as exc:
            log.debug("kg context skipped: %s", exc)
            return ""

    def recall(
        self,
        query: str,
        limit: int | None = None,
        category: str | None = None,
    ) -> list[str]:
        """Hybrid recall (vector + keyword + recency + access) with JSONB fallback."""
        limit = limit if limit is not None else self.max_context_memories
        query_emb = self._embed(query)

        blended: list[str] = []
        try:
            from brain.knowledge_graph import KnowledgeGraph
            blended = KnowledgeGraph(self).blend_descriptions(query, limit=2)
        except Exception:
            blended = []

        mem_limit = max(1, limit - len(blended)) if blended else limit

        if query_emb and self._pgvector:
            try:
                hits = self._hybrid_recall(query, query_emb, limit=mem_limit, category=category)
                if hits:
                    return (blended + hits)[:limit]
            except Exception as exc:
                log.warning("hybrid recall failed, falling back: %s", exc)

        fallback = self._fallback_recall(query_emb, limit=mem_limit, category=category)
        return (blended + fallback)[:limit]

    def _hybrid_recall(
        self,
        query: str,
        query_emb: list[float],
        *,
        limit: int,
        category: str | None,
    ) -> list[str]:
        vec = embedding_to_pgvector(query_emb)
        if not vec:
            return []

        sql = """
            SELECT id, content,
              (0.50 * (1.0 - (embedding_vec <=> %s::vector))
               + 0.25 * COALESCE(ts_rank(content_tsv, plainto_tsquery('english', %s)), 0)
               + 0.15 * (1.0 / (1.0 + EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0))
               + 0.10 * LN(1 + COALESCE(access_count, 0))
              ) AS score
            FROM memories
            WHERE embedding_vec IS NOT NULL
              AND (%s::text IS NULL OR category = %s)
            ORDER BY score DESC
            LIMIT %s
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (vec, query, category, category, limit)).fetchall()
            if not rows:
                rows = conn.execute(
                    """
                    SELECT id, content,
                      (0.60 * COALESCE(ts_rank(content_tsv, plainto_tsquery('english', %s)), 0)
                       + 0.25 * (1.0 / (1.0 + EXTRACT(EPOCH FROM (NOW() - created_at)) / 86400.0))
                       + 0.15 * LN(1 + COALESCE(access_count, 0))
                      ) AS score
                    FROM memories
                    WHERE (%s::text IS NULL OR category = %s)
                      AND content_tsv @@ plainto_tsquery('english', %s)
                    ORDER BY score DESC
                    LIMIT %s
                    """,
                    (query, category, category, query, limit),
                ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                conn.execute(
                    "UPDATE memories SET access_count = access_count + 1 WHERE id = ANY(%s)",
                    (ids,),
                )
                conn.commit()
            return [r["content"] for r in rows]

    def _fallback_recall(
        self,
        query_emb: list[float] | None,
        *,
        limit: int,
        category: str | None,
    ) -> list[str]:
        with self._conn() as conn:
            if category:
                rows = conn.execute(
                    """SELECT id, content, embedding FROM memories
                       WHERE category = %s
                       ORDER BY created_at DESC LIMIT 200""",
                    (category,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, content, embedding FROM memories ORDER BY created_at DESC LIMIT 200"
                ).fetchall()

        if not rows:
            return []

        if query_emb:
            tuples = [
                (r["id"], r["content"], json.dumps(r["embedding"]) if r["embedding"] else None)
                for r in rows
            ]
            top = rank_by_embedding(query_emb, tuples, limit=limit)
            if top:
                scored_ids = []
                for r in rows:
                    if not r["embedding"]:
                        continue
                    emb = r["embedding"]
                    if isinstance(emb, str):
                        emb = json.loads(emb)
                    scored_ids.append((cosine_similarity(query_emb, emb), r["id"]))
                scored_ids.sort(reverse=True)
                ids = [mid for _, mid in scored_ids[:limit]]
                with self._conn() as conn:
                    for mid in ids:
                        conn.execute(
                            "UPDATE memories SET access_count = access_count + 1 WHERE id = %s",
                            (mid,),
                        )
                    conn.commit()
            return top

        return [row["content"] for row in rows[:limit]]

    def recall_facts(self, query: str, limit: int | None = None) -> list[str]:
        """Hybrid recall over memory_facts when multi-tier tables exist."""
        limit = limit if limit is not None else self.max_context_memories
        if not self._table_exists("memory_facts"):
            return []
        query_emb = self._embed(query)
        text_col = self._facts_text_column()
        if not text_col:
            return []

        if query_emb and self._pgvector:
            vec = embedding_to_pgvector(query_emb)
            if vec:
                try:
                    with self._conn() as conn:
                        rows = conn.execute(
                            f"""
                            SELECT id, {text_col} AS content,
                              (0.55 * (1.0 - (embedding_vec <=> %s::vector))
                               + 0.25 * COALESCE(ts_rank(content_tsv, plainto_tsquery('english', %s)), 0)
                               + 0.20 * LN(1 + COALESCE(access_count, 0))
                              ) AS score
                            FROM memory_facts
                            WHERE embedding_vec IS NOT NULL
                              AND superseded_by IS NULL
                            ORDER BY score DESC
                            LIMIT %s
                            """,
                            (vec, query, limit),
                        ).fetchall()
                        if rows:
                            try:
                                conn.execute(
                                    "UPDATE memory_facts SET access_count = access_count + 1 WHERE id = ANY(%s)",
                                    ([r["id"] for r in rows],),
                                )
                            except Exception:
                                pass
                            conn.commit()
                            return [r["content"] for r in rows if r.get("content")]
                except Exception as exc:
                    log.warning("facts hybrid recall failed: %s", exc)

        with self._conn() as conn:
            try:
                rows = conn.execute(
                    f"""
                    SELECT {text_col} AS content FROM memory_facts
                    WHERE content_tsv @@ plainto_tsquery('english', %s)
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT %s
                    """,
                    (query, limit),
                ).fetchall()
                return [r["content"] for r in rows if r.get("content")]
            except Exception:
                rows = conn.execute(
                    f"SELECT {text_col} AS content FROM memory_facts ORDER BY id DESC LIMIT %s",
                    (limit,),
                ).fetchall()
                return [r["content"] for r in rows if r.get("content")]

    def _facts_text_column(self) -> str | None:
        cols = self._columns("memory_facts")
        for name in ("content", "fact", "text", "body"):
            if name in cols:
                return name
        return None

    def _columns(self, table: str) -> set[str]:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s""",
                    (table,),
                ).fetchall()
            return {(r["column_name"] if isinstance(r, dict) else r[0]) for r in rows}
        except Exception:
            return set()

    def _table_exists(self, name: str) -> bool:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT to_regclass(%s) AS reg", (f"public.{name}",)
                ).fetchone()
            return bool(row and row.get("reg"))
        except Exception:
            return False

    def backfill_vectors(self, limit: int = 500) -> int:
        """Copy JSONB embeddings into embedding_vec for rows missing the vector."""
        if not self._pgvector:
            with self._conn() as conn:
                self._pgvector = ensure_pgvector(conn) or pgvector_available(conn)
            if not self._pgvector:
                return 0

        updated = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, embedding FROM memories
                WHERE embedding_vec IS NULL AND embedding IS NOT NULL
                ORDER BY id
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            for row in rows:
                emb = row["embedding"]
                if isinstance(emb, str):
                    emb = json.loads(emb)
                if not isinstance(emb, list):
                    continue
                vec = embedding_to_pgvector(emb)
                if not vec:
                    continue
                conn.execute(
                    "UPDATE memories SET embedding_vec = %s::vector WHERE id = %s",
                    (vec, row["id"]),
                )
                updated += 1

            for table in ("memory_facts", "memory_episodes", "memory_procedures", "learnings"):
                if not self._table_exists(table):
                    continue
                try:
                    trows = conn.execute(
                        f"""
                        SELECT id, embedding FROM {table}
                        WHERE embedding_vec IS NULL AND embedding IS NOT NULL
                        ORDER BY id LIMIT %s
                        """,
                        (limit,),
                    ).fetchall()
                except Exception:
                    continue
                for row in trows:
                    emb = row["embedding"]
                    if isinstance(emb, str):
                        emb = json.loads(emb)
                    if not isinstance(emb, list):
                        continue
                    vec = embedding_to_pgvector(emb)
                    if not vec:
                        continue
                    conn.execute(
                        f"UPDATE {table} SET embedding_vec = %s::vector WHERE id = %s",
                        (vec, row["id"]),
                    )
                    updated += 1

            conn.commit()
        return updated

    def memory_stats(self) -> dict[str, Any]:
        """Counts + pgvector flag for health_check."""
        count = 0
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()
                count = int(row["c"] if row else 0)
                self._pgvector = self._pgvector or pgvector_available(conn)
        except Exception:
            pass
        return {"pgvector": bool(self._pgvector), "memory_count": count}

    def log_task(self, goal: str, agent_id: str = "orchestrator", conversation_id: int | None = None) -> int:
        return self.tasks.create(goal, agent_id, conversation_id or self._conversation_id)

    def log_message(self, role: str, content: str, task_id: int | None = None) -> None:
        if self._conversation_id:
            self.messages.add(self._conversation_id, role, content, task_id)

    def log_tool_steps(self, task_id: int, steps: list[dict], job_id: int | None = None) -> None:
        self.tool_calls.log_steps(task_id, steps, job_id=job_id)

    def job_forensics(self, job_id: int) -> dict | None:
        return self.job_traces.forensics(job_id)

    def job_trace_summary(self, limit: int = 5) -> dict:
        return self.job_traces.recent_forensics_summary(limit=limit)

    def set_preference(self, key: str, value: str) -> None:
        self.preferences.set(key, value)

    def complete_task(self, task_id: int, result: str, steps: list[dict]) -> None:
        self.tasks.complete(task_id, result, steps)

    def fail_task(self, task_id: int, error: str) -> None:
        self.tasks.fail(task_id, error)

    def register_agent(self, agent_id: str, name: str, role: str,
                       capabilities: list[str], spawned_by: str = "orchestrator") -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO agents (id, name, role, capabilities, spawned_by, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET
                     name = EXCLUDED.name, role = EXCLUDED.role,
                     capabilities = EXCLUDED.capabilities""",
                (agent_id, name, role, json.dumps(capabilities), spawned_by, now),
            )
            conn.commit()

    def list_agents(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM agents ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def record_learning(
        self,
        task_goal: str,
        outcome: str,
        lesson: str,
        task_id: int | None = None,
        embedding: list[float] | None = None,
        confidence: float = 0.5,
        status: str = "active",
    ) -> int:
        """Legacy-compatible insert. Prefer insert_learning from the validated loop."""
        return self.insert_learning(
            task_goal=task_goal,
            outcome=outcome,
            lesson=lesson,
            task_id=task_id,
            embedding=embedding,
            confidence=confidence,
            status=status,
            dual_remember=True,
        )

    def insert_learning(
        self,
        *,
        task_goal: str,
        outcome: str,
        lesson: str,
        task_id: int | None = None,
        embedding: list[float] | None = None,
        confidence: float = 0.5,
        status: str = "active",
        dual_remember: bool = False,
    ) -> int:
        """Insert an active (or other status) learning with optional embedding + task_id."""
        now = datetime.now(timezone.utc)
        emb = embedding if embedding is not None else self._embed(f"{task_goal}\n{lesson}")
        vec = embedding_to_pgvector(emb) if emb else None
        conf = max(0.0, min(1.0, float(confidence)))
        status = status if status in ("active", "superseded", "retracted") else "active"

        with self._conn() as conn:
            cols = self._columns("learnings")
            has_quality = "confidence" in cols
            has_vec = "embedding_vec" in cols and self._pgvector and vec

            if has_quality and has_vec:
                row = conn.execute(
                    """INSERT INTO learnings
                       (task_goal, task_id, outcome, lesson, confidence, use_count, helpful_count,
                        embedding, embedding_vec, status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s::vector, %s, %s, %s)
                       RETURNING id""",
                    (
                        task_goal,
                        task_id,
                        outcome,
                        lesson,
                        conf,
                        json.dumps(emb) if emb else None,
                        vec,
                        status,
                        now,
                        now,
                    ),
                ).fetchone()
            elif has_quality:
                row = conn.execute(
                    """INSERT INTO learnings
                       (task_goal, task_id, outcome, lesson, confidence, use_count, helpful_count,
                        embedding, status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s, %s, %s)
                       RETURNING id""",
                    (
                        task_goal,
                        task_id,
                        outcome,
                        lesson,
                        conf,
                        json.dumps(emb) if emb else None,
                        status,
                        now,
                        now,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    """INSERT INTO learnings (task_goal, task_id, outcome, lesson, created_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                    (task_goal, task_id, outcome, lesson, now),
                ).fetchone()
            conn.commit()
            lid = int(row["id"]) if row else 0

        if dual_remember and lid:
            try:
                self.remember(
                    f"[{outcome}] {lesson}",
                    category="learning",
                    metadata={"goal": task_goal, "task_id": task_id, "learning_id": lid},
                )
            except Exception:
                pass
        return lid

    def merge_learning(
        self,
        learning_id: int,
        *,
        lesson: str | None = None,
        confidence: float | None = None,
        embedding: list[float] | None = None,
        task_id: int | None = None,
        outcome: str | None = None,
    ) -> bool:
        """Bump confidence / refresh text on a corroborated duplicate."""
        now = datetime.now(timezone.utc)
        vec = embedding_to_pgvector(embedding) if embedding else None
        with self._conn() as conn:
            cols = self._columns("learnings")
            sets = ["updated_at = %s"]
            params: list[Any] = [now]
            if lesson is not None:
                sets.append("lesson = %s")
                params.append(lesson)
            if confidence is not None and "confidence" in cols:
                sets.append("confidence = %s")
                params.append(max(0.0, min(1.0, float(confidence))))
            if embedding is not None and "embedding" in cols:
                sets.append("embedding = %s")
                params.append(json.dumps(embedding))
            if vec and "embedding_vec" in cols and self._pgvector:
                sets.append("embedding_vec = %s::vector")
                params.append(vec)
            if task_id is not None:
                sets.append("task_id = COALESCE(task_id, %s)")
                params.append(task_id)
            if outcome is not None:
                sets.append("outcome = %s")
                params.append(outcome)
            if "use_count" in cols:
                sets.append("use_count = COALESCE(use_count, 0)")
            params.append(learning_id)
            conn.execute(
                f"UPDATE learnings SET {', '.join(sets)} WHERE id = %s",
                params,
            )
            conn.commit()
        return True

    def supersede_learning(self, old_id: int, new_id: int) -> bool:
        """Mark old learning superseded by new_id (status + superseded_by link)."""
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            cols = self._columns("learnings")
            if "status" in cols and "superseded_by" in cols:
                conn.execute(
                    """UPDATE learnings
                       SET status = 'superseded', superseded_by = %s, updated_at = %s
                       WHERE id = %s""",
                    (new_id, now, old_id),
                )
            elif "superseded_by" in cols:
                conn.execute(
                    "UPDATE learnings SET superseded_by = %s WHERE id = %s",
                    (new_id, old_id),
                )
            conn.commit()
        return True

    def retract_learning(self, learning_id: int) -> bool:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            cols = self._columns("learnings")
            if "status" in cols:
                conn.execute(
                    """UPDATE learnings SET status = 'retracted', updated_at = %s
                       WHERE id = %s""",
                    (now, learning_id),
                )
            conn.commit()
        return True

    def record_learning_outcome(self, learning_id: int, helped: bool) -> bool:
        """Feedback when an injected lesson helped (or not)."""
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            cols = self._columns("learnings")
            if "helpful_count" not in cols:
                return False
            if helped:
                conn.execute(
                    """UPDATE learnings
                       SET helpful_count = COALESCE(helpful_count, 0) + 1,
                           confidence = LEAST(0.98, COALESCE(confidence, 0.5) + 0.05),
                           updated_at = %s
                       WHERE id = %s""",
                    (now, learning_id),
                )
            else:
                conn.execute(
                    """UPDATE learnings
                       SET confidence = GREATEST(0.05, COALESCE(confidence, 0.5) - 0.06),
                           updated_at = %s
                       WHERE id = %s""",
                    (now, learning_id),
                )
            conn.commit()
        return True

    def bump_learning_use(self, learning_ids: list[int]) -> None:
        if not learning_ids:
            return
        with self._conn() as conn:
            cols = self._columns("learnings")
            if "use_count" not in cols:
                return
            conn.execute(
                """UPDATE learnings
                   SET use_count = COALESCE(use_count, 0) + 1, updated_at = NOW()
                   WHERE id = ANY(%s)""",
                (list(learning_ids),),
            )
            conn.commit()

    def list_active_learnings(self, limit: int = 80) -> list[dict[str, Any]]:
        with self._conn() as conn:
            cols = self._columns("learnings")
            status_filter = "WHERE status = 'active'" if "status" in cols else ""
            if "superseded_by" in cols and "status" not in cols:
                status_filter = "WHERE superseded_by IS NULL"
            rows = conn.execute(
                f"""SELECT id, task_goal, task_id, outcome, lesson, embedding,
                           created_at
                           {', confidence' if 'confidence' in cols else ''}
                           {', use_count' if 'use_count' in cols else ''}
                           {', helpful_count' if 'helpful_count' in cols else ''}
                           {', status' if 'status' in cols else ''}
                    FROM learnings {status_filter}
                    ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def find_similar_learnings(
        self,
        lesson: str,
        *,
        embedding: list[float] | None = None,
        limit: int = 12,
        min_score: float = 0.45,
    ) -> list[dict[str, Any]]:
        """Find active near-duplicates by hybrid vector+text or JSONB cosine."""
        query_emb = embedding if embedding is not None else self._embed(lesson)
        if query_emb and self._pgvector:
            try:
                hits = self._hybrid_learning_search(lesson, query_emb, limit=limit)
                if hits:
                    return [h for h in hits if float(h.get("similarity") or 0) >= min_score]
            except Exception as exc:
                log.warning("hybrid learning search failed: %s", exc)

        # JSONB / text fallback over active rows
        rows = self.list_active_learnings(limit=200)
        scored: list[dict[str, Any]] = []
        from brain.learning import text_similarity

        for row in rows:
            sim = 0.0
            emb = row.get("embedding")
            if query_emb and emb:
                if isinstance(emb, str):
                    try:
                        emb = json.loads(emb)
                    except Exception:
                        emb = None
                if isinstance(emb, list):
                    sim = cosine_similarity(query_emb, emb)
            sim = max(sim, text_similarity(lesson, row.get("lesson") or ""))
            if sim >= min_score:
                scored.append({**row, "similarity": sim})
        scored.sort(key=lambda r: r["similarity"], reverse=True)
        return scored[:limit]

    def _hybrid_learning_search(
        self,
        query: str,
        query_emb: list[float],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        vec = embedding_to_pgvector(query_emb)
        if not vec:
            return []
        with self._conn() as conn:
            cols = self._columns("learnings")
            if "embedding_vec" not in cols:
                return []
            conf_term = "0.15 * COALESCE(confidence, 0.5)" if "confidence" in cols else "0.15"
            use_term = (
                "0.10 * LN(1 + COALESCE(use_count, 0))" if "use_count" in cols else "0.10"
            )
            rows = conn.execute(
                f"""
                SELECT id, task_goal, task_id, outcome, lesson, embedding,
                       created_at,
                       COALESCE(confidence, 0.5) AS confidence,
                       COALESCE(use_count, 0) AS use_count,
                       COALESCE(helpful_count, 0) AS helpful_count,
                       status,
                       (1.0 - (embedding_vec <=> %s::vector)) AS similarity,
                       (0.55 * (1.0 - (embedding_vec <=> %s::vector))
                        + 0.20 * COALESCE(ts_rank(content_tsv, plainto_tsquery('english', %s)), 0)
                        + {conf_term}
                        + {use_term}
                       ) AS score
                FROM learnings
                WHERE status = 'active' AND embedding_vec IS NOT NULL
                ORDER BY score DESC
                LIMIT %s
                """,
                (vec, vec, query, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def learning_stats(self) -> dict[str, Any]:
        """Counts for health_check: active, superseded, avg confidence."""
        stats: dict[str, Any] = {
            "active": 0,
            "superseded": 0,
            "retracted": 0,
            "avg_confidence": 0.0,
            "total": 0,
        }
        try:
            with self._conn() as conn:
                cols = self._columns("learnings")
                if "status" not in cols:
                    row = conn.execute("SELECT COUNT(*) AS c FROM learnings").fetchone()
                    stats["active"] = int(row["c"] if row else 0)
                    stats["total"] = stats["active"]
                    return stats
                rows = conn.execute(
                    """SELECT status, COUNT(*) AS c,
                              AVG(confidence) AS avg_conf
                       FROM learnings
                       GROUP BY status"""
                ).fetchall()
                total = 0
                active_conf = None
                for r in rows:
                    st = r["status"] or "active"
                    c = int(r["c"] or 0)
                    total += c
                    if st in stats:
                        stats[st] = c
                    if st == "active" and r.get("avg_conf") is not None:
                        active_conf = float(r["avg_conf"])
                stats["total"] = total
                if active_conf is not None:
                    stats["avg_confidence"] = round(active_conf, 3)
                else:
                    row = conn.execute(
                        "SELECT AVG(confidence) AS a FROM learnings WHERE status = 'active'"
                    ).fetchone()
                    if row and row.get("a") is not None:
                        stats["avg_confidence"] = round(float(row["a"]), 3)
        except Exception as exc:
            log.debug("learning_stats failed: %s", exc)
        return stats

    def get_recent_tasks(self, limit: int = 15) -> list[dict[str, Any]]:
        return self.tasks.get_recent(limit)

    def get_failed_tasks_similar(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.tasks.get_failed_similar(goal, limit)

    def get_learnings_for_goal(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
        """Hybrid embedding retrieval of active lessons; bumps use_count on inject."""
        query_emb = self._embed(goal)
        matches: list[dict[str, Any]] = []

        if query_emb and self._pgvector:
            try:
                matches = self._hybrid_learning_search(goal, query_emb, limit=limit)
            except Exception as exc:
                log.warning("get_learnings_for_goal hybrid failed: %s", exc)
                matches = []

        if not matches:
            # Fallback: similar by lesson embedding / keyword against goal
            try:
                matches = self.find_similar_learnings(
                    goal, embedding=query_emb, limit=limit, min_score=0.35
                )
            except Exception:
                matches = []

        if not matches:
            # Keyword fallback over active rows
            goal_lower = goal.lower()
            words = [w for w in goal_lower.split() if len(w) > 3]
            for row in self.list_active_learnings(limit=100):
                tg = (row.get("task_goal") or "").lower()
                lesson = (row.get("lesson") or "").lower()
                if words and (
                    any(w in tg for w in words) or any(w in lesson for w in words)
                ):
                    matches.append({**row, "similarity": 0.4})
                if len(matches) >= limit:
                    break

        matches = matches[:limit]
        ids = [int(m["id"]) for m in matches if m.get("id") is not None]
        if ids:
            try:
                self.bump_learning_use(ids)
            except Exception:
                pass
        return matches

    def get_history_context(self, goal: str) -> str:
        parts: list[str] = []
        mem_limit = self.max_context_memories

        if self._conversation_id:
            conv_ctx = self.messages.format_context(self._conversation_id, limit=5)
            if conv_ctx:
                parts.append(conv_ctx)

        pref_ctx = self.preferences.format_for_prompt()
        if pref_ctx:
            parts.append(pref_ctx)

        recent = self.get_recent_tasks(limit=8)
        if recent:
            parts.append("## Recent tasks (what you already did)")
            for t in recent:
                result = (t.get("result") or "")[:180]
                parts.append(f"- [{t['status']}] {t['goal'][:120]} -> {result}")

        failed = self.get_failed_tasks_similar(goal, limit=4)
        if failed:
            parts.append("\n## Past failures on similar goals (do NOT repeat these)")
            for t in failed:
                parts.append(f"- FAILED: {t['goal'][:120]} -> {(t.get('result') or '')[:150]}")

        learnings = self.get_learnings_for_goal(goal, limit=5)
        if learnings:
            parts.append("\n## Lessons from similar tasks")
            for l in learnings:
                conf = l.get("confidence")
                conf_s = f" conf={float(conf):.2f}" if conf is not None else ""
                parts.append(f"- [{l.get('outcome', '?')}{conf_s}] {str(l.get('lesson') or '')[:200]}")

        semantic = self.recall(goal, limit=mem_limit)
        if semantic:
            parts.append("\n## Relevant memories")
            for m in semantic:
                parts.append(f"- {m[:200]}")

        # Multi-tier recall — facts / procedures / episodes (visible impact)
        if self.tiers_cfg.get("enabled", True):
            try:
                max_facts = int(self.tiers_cfg.get("max_facts_in_context", 5) or 5)
                max_procs = int(self.tiers_cfg.get("max_procedures_in_context", 3) or 3)
                max_eps = int(self.tiers_cfg.get("max_episodes_in_context", 4) or 4)
                facts = self.search_facts(goal, limit=max_facts)
                if facts:
                    parts.append("\n## Semantic facts (durable knowledge)")
                    for f in facts:
                        parts.append(format_fact_line(f))

                procs = self.search_procedures(goal, limit=max_procs)
                if procs:
                    parts.append("\n## Procedural patterns (how-to)")
                    for p in procs:
                        parts.append(format_procedure_line(p))

                episodes = self.search_episodes(goal, limit=max_eps)
                if not episodes:
                    episodes = self.recent_episodes(limit=max_eps)
                if episodes:
                    parts.append("\n## Recent episodes (episodic)")
                    for e in episodes:
                        parts.append(format_episode_line(e))
            except Exception as exc:
                log.debug("tier context skipped: %s", exc)

        kg_ctx = self.knowledge_graph_context(goal)
        if kg_ctx:
            parts.append("\n" + kg_ctx)

        return "\n".join(parts)

    def get_pending_inbox_tasks(self) -> list[str]:
        from brain.inbox import get_pending_inbox_tasks
        return get_pending_inbox_tasks(self.data_dir)

    def job_queue_depth(self) -> dict[str, int]:
        return self.job_queue.depth()

    def record_job_trace(
        self,
        job_id: int,
        event_type: str,
        *,
        attempt: int = 1,
        runner_id: str | None = None,
        task_id: int | None = None,
        payload: dict | None = None,
    ) -> dict | None:
        return self.job_traces.record(
            job_id,
            event_type,
            attempt=attempt,
            runner_id=runner_id,
            task_id=task_id,
            payload=payload,
        )
