"""Task history repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class TaskRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def create(self, goal: str, agent_id: str, conversation_id: int | None = None) -> int:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO tasks (goal, agent_id, conversation_id, created_at)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (goal, agent_id, conversation_id, now),
            ).fetchone()
            conn.commit()
            return row["id"] if row else 0

    def complete(self, task_id: int, result: str, steps: list[dict]) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """UPDATE tasks SET status = 'done', result = %s, steps = %s, completed_at = %s
                   WHERE id = %s""",
                (result, json.dumps(steps), now, task_id),
            )
            conn.commit()

    def fail(self, task_id: int, error: str) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET status = 'failed', result = %s, completed_at = %s WHERE id = %s",
                (error, now, task_id),
            )
            conn.commit()

    def get_recent(self, limit: int = 15) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, goal, status, result, agent_id, created_at, completed_at
                   FROM tasks ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_failed_similar(self, goal: str, limit: int = 5) -> list[dict[str, Any]]:
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
