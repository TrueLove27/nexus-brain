"""User preferences repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class PreferenceRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def set(self, key: str, value: str, source: str = "user") -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO user_preferences (key, value, source, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                (key, value, source, now, now),
            )
            conn.commit()

    def get_all(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_preferences ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def format_for_prompt(self) -> str:
        prefs = self.get_all()
        if not prefs:
            return ""
        lines = ["## User preferences (from database)"]
        for p in prefs[:20]:
            lines.append(f"- {p['key']}: {p['value']}")
        return "\n".join(lines)
