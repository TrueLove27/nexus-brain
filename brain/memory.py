"""Persistent brain — SQLite memory with semantic recall via Ollama embeddings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brain.embeddings import cosine_similarity, embed_text, rank_by_embedding
from brain.memory_tiers import (
    format_episode_line,
    format_fact_line,
    format_procedure_line,
    memory_tiers_config,
    normalize_source,
)


class BrainMemory:
    def __init__(
        self,
        db_path: Path,
        embed_model: str,
        ollama_url: str,
        brain_cfg: dict | None = None,
    ):
        self.db_path = db_path
        self.data_dir = db_path.parent
        self.embed_model = embed_model
        self.ollama_url = ollama_url.rstrip("/")
        self.storage_type = "sqlite"
        self.tiers_cfg = memory_tiers_config(brain_cfg)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    embedding TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    result TEXT,
                    agent_id TEXT,
                    steps TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    capabilities TEXT DEFAULT '[]',
                    spawned_by TEXT,
                    created_at TEXT NOT NULL,
                    task_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_goal TEXT,
                    task_id INTEGER,
                    outcome TEXT,
                    lesson TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    use_count INTEGER DEFAULT 0,
                    helpful_count INTEGER DEFAULT 0,
                    embedding TEXT,
                    superseded_by INTEGER,
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                );

                -- Multi-tier memory (episodic → semantic → procedural)
                CREATE TABLE IF NOT EXISTS memory_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    task_id INTEGER,
                    conversation_id INTEGER,
                    job_id INTEGER,
                    content TEXT NOT NULL,
                    embedding TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    consolidated_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mem_ep_created
                    ON memory_episodes (created_at);
                CREATE INDEX IF NOT EXISTS idx_mem_ep_source
                    ON memory_episodes (source);

                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    embedding TEXT,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    source_episode_ids TEXT DEFAULT '[]',
                    superseded_by INTEGER,
                    access_count INTEGER DEFAULT 0,
                    category TEXT DEFAULT 'general',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mem_facts_created
                    ON memory_facts (created_at);
                CREATE INDEX IF NOT EXISTS idx_mem_facts_category
                    ON memory_facts (category);

                CREATE TABLE IF NOT EXISTS memory_procedures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_text TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT,
                    confidence REAL NOT NULL DEFAULT 0.7,
                    use_count INTEGER DEFAULT 0,
                    category TEXT DEFAULT 'general',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mem_procs_created
                    ON memory_procedures (created_at);
                CREATE INDEX IF NOT EXISTS idx_mem_procs_category
                    ON memory_procedures (category);

                -- Project / entity knowledge graph
                CREATE TABLE IF NOT EXISTS kg_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL
                        CHECK (entity_type IN ('project','file','tool','concept','person','repo','other')),
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    description TEXT,
                    embedding TEXT,
                    metadata TEXT DEFAULT '{}',
                    mention_count INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (entity_type, normalized_name)
                );
                CREATE INDEX IF NOT EXISTS idx_kg_entities_normalized
                    ON kg_entities (normalized_name);
                CREATE INDEX IF NOT EXISTS idx_kg_entities_type
                    ON kg_entities (entity_type);

                CREATE TABLE IF NOT EXISTS kg_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
                    to_id INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
                    relation_type TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    evidence TEXT,
                    task_id INTEGER,
                    created_at TEXT NOT NULL,
                    CHECK (from_id <> to_id)
                );
                CREATE INDEX IF NOT EXISTS idx_kg_relations_from
                    ON kg_relations (from_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_kg_relations_to
                    ON kg_relations (to_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_kg_relations_type
                    ON kg_relations (relation_type);
            """)
            # Validated learning quality columns (idempotent for older DBs)
            existing = {
                r[1] for r in conn.execute("PRAGMA table_info(learnings)").fetchall()
            }
            alters = [
                ("task_id", "ALTER TABLE learnings ADD COLUMN task_id INTEGER"),
                ("confidence", "ALTER TABLE learnings ADD COLUMN confidence REAL DEFAULT 0.5"),
                ("use_count", "ALTER TABLE learnings ADD COLUMN use_count INTEGER DEFAULT 0"),
                ("helpful_count", "ALTER TABLE learnings ADD COLUMN helpful_count INTEGER DEFAULT 0"),
                ("embedding", "ALTER TABLE learnings ADD COLUMN embedding TEXT"),
                ("superseded_by", "ALTER TABLE learnings ADD COLUMN superseded_by INTEGER"),
                ("status", "ALTER TABLE learnings ADD COLUMN status TEXT DEFAULT 'active'"),
                ("updated_at", "ALTER TABLE learnings ADD COLUMN updated_at TEXT"),
            ]
            for col, sql in alters:
                if col not in existing:
                    try:
                        conn.execute(sql)
                    except Exception:
                        pass

    def _embed(self, text: str) -> list[float] | None:
        return embed_text(text, self.embed_model, self.ollama_url)

    def remember(self, content: str, category: str = "general", metadata: dict | None = None) -> int:
        """Dual-write: legacy `memories` bag + episodic tier."""
        embedding = self._embed(content)
        now = datetime.now(timezone.utc).isoformat()
        meta = metadata or {}
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO memories (content, category, embedding, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (content, category, json.dumps(embedding) if embedding else None,
                 json.dumps(meta), now),
            )
            mid = cur.lastrowid or 0
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
                conversation_id=meta.get("conversation_id"),
                job_id=meta.get("job_id"),
                embedding=embedding,
                metadata={**meta, "legacy_memory_id": mid, "category": category},
            )
        except Exception:
            pass
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
        now = datetime.now(timezone.utc).isoformat()
        source = normalize_source(source)
        if embedding is None:
            embedding = self._embed(content)
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO memory_episodes
                   (source, task_id, conversation_id, job_id, content, embedding, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source,
                    task_id,
                    conversation_id,
                    job_id,
                    content,
                    json.dumps(embedding) if embedding else None,
                    json.dumps(metadata or {}),
                    now,
                ),
            )
            return cur.lastrowid or 0

    def get_unconsolidated_episodes(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, source, task_id, conversation_id, job_id, content,
                          embedding, metadata, created_at, consolidated_at
                   FROM memory_episodes
                   WHERE consolidated_at IS NULL
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_unconsolidated_episodes(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_episodes WHERE consolidated_at IS NULL"
            ).fetchone()
        return int(row["n"] if row else 0)

    def mark_episodes_consolidated(self, episode_ids: list[int]) -> int:
        if not episode_ids:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for eid in episode_ids:
                conn.execute(
                    """UPDATE memory_episodes
                       SET consolidated_at = ?
                       WHERE id = ? AND consolidated_at IS NULL""",
                    (now, eid),
                )
        return len(episode_ids)

    def insert_fact(
        self,
        content: str,
        *,
        confidence: float = 0.7,
        source_episode_ids: list[int] | None = None,
        category: str = "general",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        embedding = self._embed(content)
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO memory_facts
                   (content, embedding, confidence, source_episode_ids, category, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    content,
                    json.dumps(embedding) if embedding else None,
                    max(0.0, min(1.0, float(confidence))),
                    json.dumps(source_episode_ids or []),
                    category or "general",
                    now,
                    now,
                ),
            )
            return cur.lastrowid or 0

    def insert_procedure(
        self,
        trigger_text: str,
        content: str,
        *,
        confidence: float = 0.7,
        category: str = "general",
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        embedding = self._embed(f"{trigger_text}\n{content}")
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO memory_procedures
                   (trigger_text, content, embedding, confidence, category, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    trigger_text,
                    content,
                    json.dumps(embedding) if embedding else None,
                    max(0.0, min(1.0, float(confidence))),
                    category or "general",
                    now,
                ),
            )
            return cur.lastrowid or 0

    def mark_fact_superseded(self, fact_id: int, superseded_by: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE memory_facts SET superseded_by = ?, updated_at = ? WHERE id = ?",
                (superseded_by, now, fact_id),
            )

    def _rank_table(
        self,
        query: str,
        rows: list,
        limit: int,
        *,
        text_key: str = "content",
        extra_key: str | None = None,
        bump_ids: list[int] | None = None,
        bump_sql: str | None = None,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        dicts = [dict(r) for r in rows]
        query_emb = self._embed(query)
        hits: list[dict[str, Any]] = []
        if query_emb:
            scored: list[tuple[float, dict]] = []
            for d in dicts:
                raw = d.get("embedding")
                if not raw:
                    continue
                emb = json.loads(raw) if isinstance(raw, str) else raw
                scored.append((cosine_similarity(query_emb, emb), d))
            scored.sort(key=lambda x: x[0], reverse=True)
            hits = [d for s, d in scored[:limit] if s > 0.25]
        if not hits:
            words = [w.lower() for w in query.split() if len(w) > 3][:8]
            if words:
                scored_kw = []
                for d in dicts:
                    text = (d.get(text_key) or "").lower()
                    if extra_key:
                        text += " " + (d.get(extra_key) or "").lower()
                    score = sum(1 for w in words if w in text)
                    if score:
                        scored_kw.append((score, d))
                scored_kw.sort(key=lambda x: x[0], reverse=True)
                hits = [s[1] for s in scored_kw[:limit]]
            else:
                hits = dicts[:limit]
        if bump_sql and hits:
            with self._conn() as conn:
                for h in hits:
                    conn.execute(bump_sql, (h["id"],))
        return hits[:limit]

    def search_facts(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, content, embedding, confidence, source_episode_ids,
                          superseded_by, access_count, category, created_at, updated_at
                   FROM memory_facts
                   WHERE superseded_by IS NULL
                   ORDER BY created_at DESC LIMIT 200"""
            ).fetchall()
        return self._rank_table(
            query,
            rows,
            limit,
            bump_sql="UPDATE memory_facts SET access_count = access_count + 1, updated_at = datetime('now') WHERE id = ?",
        )

    def search_procedures(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, trigger_text, content, embedding, confidence,
                          use_count, category, created_at
                   FROM memory_procedures
                   ORDER BY created_at DESC LIMIT 200"""
            ).fetchall()
        return self._rank_table(
            query,
            rows,
            limit,
            text_key="trigger_text",
            extra_key="content",
            bump_sql="UPDATE memory_procedures SET use_count = use_count + 1 WHERE id = ?",
        )

    def search_episodes(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, source, content, embedding, created_at, metadata
                   FROM memory_episodes
                   ORDER BY created_at DESC LIMIT 100"""
            ).fetchall()
        return self._rank_table(query, rows, limit)

    def recent_episodes(self, limit: int = 4) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, source, content, embedding, created_at, metadata
                   FROM memory_episodes
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def memory_tier_stats(self) -> dict[str, Any]:
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
            "enabled": bool(self.tiers_cfg.get("enabled", True)),
            "episodes": int(ep["n"] if ep else 0),
            "unconsolidated": int(unc["n"] if unc else 0),
            "facts": int(facts["n"] if facts else 0),
            "procedures": int(procs["n"] if procs else 0),
            "consolidate_every_n_episodes": int(
                self.tiers_cfg.get("consolidate_every_n_episodes", 5) or 5
            ),
        }

    def knowledge_graph_stats(self) -> dict[str, int]:
        try:
            from brain.knowledge_graph import KnowledgeGraph
            return KnowledgeGraph(self).stats()
        except Exception:
            return {"kg_entities": 0, "kg_relations": 0}

    def knowledge_graph_context(self, goal: str) -> str:
        try:
            from brain.knowledge_graph import KnowledgeGraph
            return KnowledgeGraph(self).context_for_goal(goal)
        except Exception:
            return ""

    def recall(self, query: str, limit: int = 8) -> list[str]:
        query_emb = self._embed(query)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, content, embedding FROM memories ORDER BY created_at DESC LIMIT 200"
            ).fetchall()

        blended: list[str] = []
        try:
            from brain.knowledge_graph import KnowledgeGraph
            blended = KnowledgeGraph(self).blend_descriptions(query, limit=2)
        except Exception:
            blended = []

        if not rows:
            return blended[:limit]

        if query_emb:
            tuples = [(r["id"], r["content"], r["embedding"]) for r in rows]
            top = rank_by_embedding(query_emb, tuples, limit=max(1, limit - len(blended)))
            if top:
                ids = [t[0] for t in sorted(
                    [(cosine_similarity(query_emb, json.loads(r["embedding"])), r["id"])
                     for r in rows if r["embedding"]],
                    reverse=True,
                )[:limit]]
                with self._conn() as conn:
                    for mid in ids:
                        conn.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (mid,))
            return (blended + top)[:limit]

        return (blended + [row["content"] for row in rows])[:limit]

    def log_task(self, goal: str, agent_id: str = "orchestrator") -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (goal, agent_id, created_at) VALUES (?, ?, ?)",
                (goal, agent_id, now),
            )
            return cur.lastrowid or 0

    def complete_task(self, task_id: int, result: str, steps: list[dict]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'done', result = ?, steps = ?, completed_at = ? WHERE id = ?",
                (result, json.dumps(steps), now, task_id),
            )

    def fail_task(self, task_id: int, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'failed', result = ?, completed_at = ? WHERE id = ?",
                (error, now, task_id),
            )

    def register_agent(self, agent_id: str, name: str, role: str,
                       capabilities: list[str], spawned_by: str = "orchestrator") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO agents (id, name, role, capabilities, spawned_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (agent_id, name, role, json.dumps(capabilities), spawned_by, now),
            )

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
        now = datetime.now(timezone.utc).isoformat()
        emb = embedding if embedding is not None else self._embed(f"{task_goal}\n{lesson}")
        conf = max(0.0, min(1.0, float(confidence)))
        status = status if status in ("active", "superseded", "retracted") else "active"
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO learnings
                   (task_goal, task_id, outcome, lesson, confidence, use_count, helpful_count,
                    embedding, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)""",
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
            )
            lid = cur.lastrowid or 0
        if dual_remember and lid:
            self.remember(
                f"[{outcome}] {lesson}",
                category="learning",
                metadata={"goal": task_goal, "task_id": task_id, "learning_id": lid},
            )
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
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM learnings WHERE id = ?", (learning_id,)).fetchone()
            if not row:
                return False
            new_lesson = lesson if lesson is not None else row["lesson"]
            new_conf = (
                max(0.0, min(1.0, float(confidence)))
                if confidence is not None
                else (row["confidence"] if "confidence" in row.keys() else 0.5)
            )
            new_emb = json.dumps(embedding) if embedding is not None else row["embedding"]
            new_task = task_id if task_id is not None else row["task_id"] if "task_id" in row.keys() else None
            new_outcome = outcome if outcome is not None else row["outcome"]
            conn.execute(
                """UPDATE learnings
                   SET lesson = ?, confidence = ?, embedding = ?, task_id = COALESCE(task_id, ?),
                       outcome = ?, updated_at = ?
                   WHERE id = ?""",
                (new_lesson, new_conf, new_emb, new_task, new_outcome, now, learning_id),
            )
        return True

    def supersede_learning(self, old_id: int, new_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """UPDATE learnings SET status = 'superseded', superseded_by = ?, updated_at = ?
                   WHERE id = ?""",
                (new_id, now, old_id),
            )
        return True

    def retract_learning(self, learning_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "UPDATE learnings SET status = 'retracted', updated_at = ? WHERE id = ?",
                (now, learning_id),
            )
        return True

    def record_learning_outcome(self, learning_id: int, helped: bool) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT confidence, helpful_count FROM learnings WHERE id = ?", (learning_id,)
            ).fetchone()
            if not row:
                return False
            conf = float(row["confidence"] or 0.5)
            if helped:
                conn.execute(
                    """UPDATE learnings
                       SET helpful_count = COALESCE(helpful_count, 0) + 1,
                           confidence = ?, updated_at = ?
                       WHERE id = ?""",
                    (min(0.98, conf + 0.05), now, learning_id),
                )
            else:
                conn.execute(
                    """UPDATE learnings SET confidence = ?, updated_at = ? WHERE id = ?""",
                    (max(0.05, conf - 0.06), now, learning_id),
                )
        return True

    def bump_learning_use(self, learning_ids: list[int]) -> None:
        if not learning_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for lid in learning_ids:
                conn.execute(
                    """UPDATE learnings
                       SET use_count = COALESCE(use_count, 0) + 1, updated_at = ?
                       WHERE id = ?""",
                    (now, lid),
                )

    def list_active_learnings(self, limit: int = 80) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, task_goal, task_id, outcome, lesson, embedding,
                          confidence, use_count, helpful_count, status, created_at
                   FROM learnings
                   WHERE COALESCE(status, 'active') = 'active'
                   ORDER BY created_at DESC LIMIT ?""",
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
        from brain.learning import text_similarity

        query_emb = embedding if embedding is not None else self._embed(lesson)
        scored: list[dict[str, Any]] = []
        for row in self.list_active_learnings(limit=200):
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

    def learning_stats(self) -> dict[str, Any]:
        stats = {"active": 0, "superseded": 0, "retracted": 0, "avg_confidence": 0.0, "total": 0}
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT COALESCE(status, 'active') AS status, COUNT(*) AS c,
                          AVG(COALESCE(confidence, 0.5)) AS avg_conf
                   FROM learnings GROUP BY COALESCE(status, 'active')"""
            ).fetchall()
            total = 0
            for r in rows:
                st = r["status"] or "active"
                c = int(r["c"] or 0)
                total += c
                if st in stats:
                    stats[st] = c
                if st == "active" and r["avg_conf"] is not None:
                    stats["avg_confidence"] = round(float(r["avg_conf"]), 3)
            stats["total"] = total
        return stats

    def get_recent_tasks(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, goal, status, result, agent_id, created_at, completed_at
                   FROM tasks ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_failed_tasks_similar(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
        words = [w.lower() for w in goal.split() if len(w) > 3][:6]
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, goal, status, result, created_at FROM tasks
                   WHERE status IN ('failed', 'incomplete') ORDER BY created_at DESC LIMIT 50"""
            ).fetchall()
        scored = []
        for row in rows:
            g = row["goal"].lower()
            score = sum(1 for w in words if w in g)
            if score > 0:
                scored.append((score, dict(row)))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in scored[:limit]]

    def get_learnings_for_goal(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
        """Embedding-ranked active lessons; bumps use_count when injected."""
        query_emb = self._embed(goal)
        matches = self.find_similar_learnings(
            goal, embedding=query_emb, limit=limit, min_score=0.35
        )
        if not matches:
            goal_lower = goal.lower()
            words = [w for w in goal_lower.split() if len(w) > 3]
            for row in self.list_active_learnings(limit=100):
                tg = (row.get("task_goal") or "").lower()
                lesson = (row.get("lesson") or "").lower()
                if words and (any(w in tg for w in words) or any(w in lesson for w in words)):
                    matches.append({**row, "similarity": 0.4})
                if len(matches) >= limit:
                    break
        matches = matches[:limit]
        ids = [int(m["id"]) for m in matches if m.get("id") is not None]
        if ids:
            self.bump_learning_use(ids)
        return matches

    def get_history_context(self, goal: str, *, cognitive: bool = True) -> str:
        """Formatted history so agents avoid repeating past mistakes.

        cognitive=True (default): full stack including memories/facts/procs/learnings/KG.
        cognitive=False: operational only (recent tasks + failures) — use when the
        specialist system prompt already carries goal-conditioned cognitive context.
        """
        parts: list[str] = []
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

        if not cognitive:
            return "\n".join(parts)

        learnings = self.get_learnings_for_goal(goal, limit=5)
        if learnings:
            parts.append("\n## Lessons from similar tasks")
            for l in learnings:
                conf = l.get("confidence")
                conf_s = f" conf={float(conf):.2f}" if conf is not None else ""
                parts.append(f"- [{l.get('outcome', '?')}{conf_s}] {str(l.get('lesson') or '')[:200]}")

        semantic = self.recall(goal, limit=5)
        if semantic:
            parts.append("\n## Relevant memories")
            for m in semantic:
                parts.append(f"- {m[:200]}")

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
            except Exception:
                pass

        kg_ctx = self.knowledge_graph_context(goal)
        if kg_ctx:
            parts.append("\n" + kg_ctx)

        return "\n".join(parts)

    def get_pending_inbox_tasks(self) -> list[str]:
        from brain.inbox import get_pending_inbox_tasks
        return get_pending_inbox_tasks(self.data_dir)

    def ensure_conversation(self, session_key: str = "default") -> int:
        return 0

    def log_message(self, role: str, content: str, task_id: int | None = None) -> None:
        pass

    def log_tool_steps(self, task_id: int, steps: list[dict], job_id: int | None = None) -> None:
        pass

    def set_preference(self, key: str, value: str) -> None:
        pass

    @property
    def conversation_id(self) -> int | None:
        return None
