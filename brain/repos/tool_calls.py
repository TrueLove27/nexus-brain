"""Normalized tool call steps repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class ToolCallRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def log_steps(self, task_id: int, steps: list[dict], job_id: int | None = None) -> None:
        if not task_id or not steps:
            return
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            for s in steps:
                action = s.get("action", "unknown")
                if action in ("finish", "delegate", "create_agent"):
                    continue
                conn.execute(
                    """INSERT INTO tool_calls (task_id, job_id, iteration, action, args, result, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        task_id,
                        job_id,
                        s.get("iteration", 0),
                        action,
                        json.dumps(s.get("args") or {}),
                        (s.get("result") or "")[:4000],
                        now,
                    ),
                )
            conn.commit()

    def log_event(
        self,
        task_id: int | None,
        event_type: str,
        payload: dict,
        job_id: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        # Prefer explicit job_id; fall back to payload if callers embed it
        resolved_job = job_id if job_id is not None else payload.get("job_id")
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO agent_events (task_id, job_id, event_type, payload, created_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (task_id, resolved_job, event_type, json.dumps(payload), now),
            )
            conn.commit()

    def get_for_task(self, task_id: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, action, args, result, iteration, job_id, created_at FROM tool_calls
                   WHERE task_id = %s ORDER BY iteration, id""",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_for_job(self, job_id: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, task_id, action, args, result, iteration, job_id, created_at
                   FROM tool_calls WHERE job_id = %s
                   ORDER BY iteration ASC, id ASC""",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_events_for_job(self, job_id: int) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, task_id, job_id, event_type, payload, created_at
                   FROM agent_events WHERE job_id = %s
                   ORDER BY created_at ASC, id ASC""",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]
