"""Chat message repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class MessageRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def add(self, conversation_id: int, role: str, content: str, task_id: int | None = None) -> int:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO messages (conversation_id, role, content, task_id, created_at)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (conversation_id, role, content, task_id, now),
            ).fetchone()
            conn.commit()
            return row["id"] if row else 0

    def get_recent(self, conversation_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if conversation_id:
                rows = conn.execute(
                    """SELECT role, content, created_at FROM messages
                       WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s""",
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT role, content, created_at FROM messages
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def format_context(self, conversation_id: int | None = None, limit: int = 5) -> str:
        msgs = self.get_recent(conversation_id, limit=limit)
        if not msgs:
            return ""
        lines = ["## Recent conversation"]
        for m in msgs:
            role = "User" if m["role"] == "user" else "Nexus"
            lines.append(f"- {role}: {m['content'][:200]}")
        return "\n".join(lines)
