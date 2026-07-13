"""PostgreSQL-backed brain memory — durable historical record for the agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from brain.embeddings import cosine_similarity, embed_text, rank_by_embedding
from brain.repos.conversations import ConversationRepo
from brain.repos.job_queue import JobQueueRepo
from brain.repos.messages import MessageRepo
from brain.repos.preferences import PreferenceRepo
from brain.repos.tasks import TaskRepo
from brain.repos.tool_calls import ToolCallRepo
from db.migrate import run_migrations


class PostgresMemory:
    def __init__(self, dsn: str, data_dir: Path, embed_model: str, ollama_url: str):
        self.dsn = dsn
        self.data_dir = data_dir
        self.embed_model = embed_model
        self.ollama_url = ollama_url.rstrip("/")
        self.storage_type = "postgres"
        self.db_path = data_dir / "postgres.marker"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.conversations = ConversationRepo(self._conn)
        self.messages = MessageRepo(self._conn)
        self.tasks = TaskRepo(self._conn)
        self.tool_calls = ToolCallRepo(self._conn)
        self.preferences = PreferenceRepo(self._conn)
        self.job_queue = JobQueueRepo(self._conn)
        self._conversation_id: int | None = None

    def _conn(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _init_db(self) -> None:
        run_migrations(self.dsn)

    def ensure_conversation(self, session_key: str = "default") -> int:
        self._conversation_id = self.conversations.get_or_create(session_key)
        return self._conversation_id

    @property
    def conversation_id(self) -> int | None:
        return self._conversation_id

    def _embed(self, text: str) -> list[float] | None:
        return embed_text(text, self.embed_model, self.ollama_url)

    def remember(self, content: str, category: str = "general", metadata: dict | None = None) -> int:
        embedding = self._embed(content)
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO memories (content, category, embedding, metadata, created_at)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (content, category, json.dumps(embedding) if embedding else None,
                 json.dumps(metadata or {}), now),
            ).fetchone()
            conn.commit()
            return row["id"] if row else 0

    def recall(self, query: str, limit: int = 8) -> list[str]:
        query_emb = self._embed(query)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, content, embedding FROM memories ORDER BY created_at DESC LIMIT 200"
            ).fetchall()

        if not rows:
            return []

        if query_emb:
            tuples = [(r["id"], r["content"], json.dumps(r["embedding"]) if r["embedding"] else None) for r in rows]
            top = rank_by_embedding(query_emb, tuples, limit=limit)
            if top:
                ids = [t[0] for t in sorted(
                    [(cosine_similarity(query_emb, r["embedding"]), r["id"])
                     for r in rows if r["embedding"]],
                    reverse=True,
                )[:limit]]
                with self._conn() as conn:
                    for mid in ids:
                        conn.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = %s", (mid,))
                    conn.commit()
            return top

        return [row["content"] for row in rows[:limit]]

    def log_task(self, goal: str, agent_id: str = "orchestrator", conversation_id: int | None = None) -> int:
        return self.tasks.create(goal, agent_id, conversation_id or self._conversation_id)

    def log_message(self, role: str, content: str, task_id: int | None = None) -> None:
        if self._conversation_id:
            self.messages.add(self._conversation_id, role, content, task_id)

    def log_tool_steps(self, task_id: int, steps: list[dict]) -> None:
        self.tool_calls.log_steps(task_id, steps)

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

    def record_learning(self, task_goal: str, outcome: str, lesson: str) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO learnings (task_goal, outcome, lesson, created_at) VALUES (%s, %s, %s, %s)",
                (task_goal, outcome, lesson, now),
            )
            conn.commit()
        self.remember(f"[{outcome}] {lesson}", category="learning", metadata={"goal": task_goal})

    def get_recent_tasks(self, limit: int = 15) -> list[dict[str, Any]]:
        return self.tasks.get_recent(limit)

    def get_failed_tasks_similar(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.tasks.get_failed_similar(goal, limit)

    def get_learnings_for_goal(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
        recalled = self.recall(goal, limit=limit)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT task_goal, outcome, lesson, created_at FROM learnings ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        matches = []
        goal_lower = goal.lower()
        for row in rows:
            tg = (row["task_goal"] or "").lower()
            lesson = row["lesson"]
            if tg and any(w in tg for w in goal_lower.split() if len(w) > 3):
                matches.append(dict(row))
            elif lesson in recalled:
                matches.append(dict(row))
        return matches[:limit]

    def get_history_context(self, goal: str) -> str:
        parts: list[str] = []

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
                parts.append(f"- [{l['outcome']}] {l['lesson'][:200]}")

        semantic = self.recall(goal, limit=5)
        if semantic:
            parts.append("\n## Relevant memories")
            for m in semantic:
                parts.append(f"- {m[:200]}")

        return "\n".join(parts)

    def get_pending_inbox_tasks(self) -> list[str]:
        from brain.inbox import get_pending_inbox_tasks
        return get_pending_inbox_tasks(self.data_dir)

    def job_queue_depth(self) -> dict[str, int]:
        return self.job_queue.depth()
