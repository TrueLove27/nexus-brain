"""Persistent brain — SQLite memory with semantic recall via Ollama embeddings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from brain.embeddings import cosine_similarity, embed_text, rank_by_embedding


class BrainMemory:
    def __init__(self, db_path: Path, embed_model: str, ollama_url: str):
        self.db_path = db_path
        self.data_dir = db_path.parent
        self.embed_model = embed_model
        self.ollama_url = ollama_url.rstrip("/")
        self.storage_type = "sqlite"
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
                    outcome TEXT,
                    lesson TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    def _embed(self, text: str) -> list[float] | None:
        return embed_text(text, self.embed_model, self.ollama_url)

    def remember(self, content: str, category: str = "general", metadata: dict | None = None) -> int:
        embedding = self._embed(content)
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO memories (content, category, embedding, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (content, category, json.dumps(embedding) if embedding else None,
                 json.dumps(metadata or {}), now),
            )
            return cur.lastrowid or 0

    def recall(self, query: str, limit: int = 8) -> list[str]:
        query_emb = self._embed(query)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, content, embedding FROM memories ORDER BY created_at DESC LIMIT 200"
            ).fetchall()

        if not rows:
            return []

        if query_emb:
            tuples = [(r["id"], r["content"], r["embedding"]) for r in rows]
            top = rank_by_embedding(query_emb, tuples, limit=limit)
            if top:
                ids = [t[0] for t in sorted(
                    [(cosine_similarity(query_emb, json.loads(r["embedding"])), r["id"])
                     for r in rows if r["embedding"]],
                    reverse=True,
                )[:limit]]
                with self._conn() as conn:
                    for mid in ids:
                        conn.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (mid,))
            return top

        return [row["content"] for row in rows[:limit]]

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

    def record_learning(self, task_goal: str, outcome: str, lesson: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO learnings (task_goal, outcome, lesson, created_at) VALUES (?, ?, ?, ?)",
                (task_goal, outcome, lesson, now),
            )
        self.remember(f"[{outcome}] {lesson}", category="learning", metadata={"goal": task_goal})

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
        """Formatted history so agents avoid repeating past mistakes."""
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
        inbox = self.data_dir / "inbox"
        if not inbox.exists():
            return []
        tasks = []
        for f in sorted(inbox.glob("*.txt")):
            tasks.append(f.read_text(encoding="utf-8").strip())
            processed = inbox / "processed"
            processed.mkdir(exist_ok=True)
            f.rename(processed / f.name)
        return tasks

    def ensure_conversation(self, session_key: str = "default") -> int:
        return 0

    def log_message(self, role: str, content: str, task_id: int | None = None) -> None:
        pass

    def log_tool_steps(self, task_id: int, steps: list[dict]) -> None:
        pass

    def set_preference(self, key: str, value: str) -> None:
        pass

    @property
    def conversation_id(self) -> int | None:
        return None
