"""Conversation session repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ConversationRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def get_or_create(self, session_key: str = "default") -> int:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id FROM conversations
                   WHERE session_key = %s AND ended_at IS NULL
                   ORDER BY started_at DESC LIMIT 1""",
                (session_key,),
            ).fetchone()
            if row:
                return row["id"]
            row = conn.execute(
                "INSERT INTO conversations (session_key, started_at) VALUES (%s, %s) RETURNING id",
                (session_key, now),
            ).fetchone()
            conn.commit()
            return row["id"] if row else 0

    def end_session(self, conversation_id: int) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                "UPDATE conversations SET ended_at = %s WHERE id = %s",
                (now, conversation_id),
            )
            conn.commit()

    def get_last_active_goal(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT t.goal FROM tasks t
                   JOIN conversations c ON t.conversation_id = c.id
                   WHERE c.ended_at IS NULL
                   ORDER BY t.created_at DESC LIMIT 1"""
            ).fetchone()
        return row["goal"] if row else None
