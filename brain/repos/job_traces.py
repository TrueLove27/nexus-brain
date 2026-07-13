"""Per-job execution traces — lifecycle events + forensics aggregation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


class JobTraceRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def record(
        self,
        job_id: int,
        event_type: str,
        *,
        attempt: int = 1,
        runner_id: str | None = None,
        task_id: int | None = None,
        payload: dict | None = None,
    ) -> dict[str, Any] | None:
        if not job_id or not event_type:
            return None
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO job_traces
                   (job_id, attempt, runner_id, task_id, event_type, payload, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    job_id,
                    max(1, int(attempt or 1)),
                    runner_id,
                    task_id,
                    event_type,
                    json.dumps(payload or {}),
                    now,
                ),
            ).fetchone()
            conn.commit()
        return dict(row) if row else None

    def list_for_job(self, job_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, job_id, attempt, runner_id, task_id, event_type, payload, created_at
                   FROM job_traces WHERE job_id = %s
                   ORDER BY created_at ASC, id ASC
                   LIMIT %s""",
                (job_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent(
        self,
        limit: int = 20,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if event_type:
                rows = conn.execute(
                    """SELECT id, job_id, attempt, runner_id, task_id, event_type, payload, created_at
                       FROM job_traces WHERE event_type = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, job_id, attempt, runner_id, task_id, event_type, payload, created_at
                       FROM job_traces ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def forensics(self, job_id: int) -> dict[str, Any] | None:
        """Full failure/lease forensics package for one durable job."""
        with self._conn() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
            if not job:
                return None

            traces = conn.execute(
                """SELECT id, job_id, attempt, runner_id, task_id, event_type, payload, created_at
                   FROM job_traces WHERE job_id = %s
                   ORDER BY created_at ASC, id ASC""",
                (job_id,),
            ).fetchall()

            tool_calls = conn.execute(
                """SELECT id, task_id, job_id, iteration, action, args, result, duration_ms, created_at
                   FROM tool_calls WHERE job_id = %s
                   ORDER BY iteration ASC, id ASC""",
                (job_id,),
            ).fetchall()

            events = conn.execute(
                """SELECT id, task_id, job_id, event_type, payload, created_at
                   FROM agent_events WHERE job_id = %s
                   ORDER BY created_at ASC, id ASC""",
                (job_id,),
            ).fetchall()

            # Tasks linked via traces or tool_calls
            task_ids = {
                int(r["task_id"])
                for r in list(traces) + list(tool_calls) + list(events)
                if r.get("task_id") is not None
            }
            tasks: list[dict] = []
            if task_ids:
                rows = conn.execute(
                    """SELECT id, goal, status, result, agent_id, created_at, completed_at
                       FROM tasks WHERE id = ANY(%s) ORDER BY id""",
                    (list(task_ids),),
                ).fetchall()
                tasks = [dict(r) for r in rows]

        job_dict = dict(job)
        reclaim_count = sum(1 for t in traces if t["event_type"] == "lease_reclaimed")
        fenced_count = sum(1 for t in traces if t["event_type"] == "fenced_out")
        resumed_count = sum(1 for t in traces if t["event_type"] == "resumed")
        cp = job_dict.get("checkpoint") or {}
        if isinstance(cp, str):
            try:
                cp = json.loads(cp)
            except json.JSONDecodeError:
                cp = {}
        if not isinstance(cp, dict):
            cp = {}
        cp_steps = cp.get("steps") if isinstance(cp.get("steps"), list) else []
        return {
            "job": job_dict,
            "traces": [dict(r) for r in traces],
            "tool_calls": [dict(r) for r in tool_calls],
            "agent_events": [dict(r) for r in events],
            "tasks": tasks,
            "summary": {
                "attempts": int(job_dict.get("attempts") or 0),
                "status": job_dict.get("status"),
                "lease_reclaims": reclaim_count,
                "fenced_out": fenced_count,
                "resumes": resumed_count,
                "fence_token": int(job_dict.get("fence_token") or 0),
                "checkpoint_steps": len(cp_steps),
                "checkpoint_next_iteration": cp.get("next_iteration"),
                "tool_call_count": len(tool_calls),
                "agent_event_count": len(events),
                "trace_count": len(traces),
                "last_error": job_dict.get("error"),
            },
        }

    def recent_forensics_summary(self, limit: int = 5) -> dict[str, Any]:
        """Compact health/status surface: latest fails + lease reclaims."""
        with self._conn() as conn:
            reclaims = conn.execute(
                """SELECT jt.job_id, jt.attempt, jt.runner_id, jt.payload, jt.created_at,
                          j.goal, j.status, j.error
                   FROM job_traces jt
                   JOIN jobs j ON j.id = jt.job_id
                   WHERE jt.event_type = 'lease_reclaimed'
                   ORDER BY jt.created_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
            fails = conn.execute(
                """SELECT jt.job_id, jt.attempt, jt.runner_id, jt.payload, jt.created_at,
                          j.goal, j.status, j.error
                   FROM job_traces jt
                   JOIN jobs j ON j.id = jt.job_id
                   WHERE jt.event_type IN ('failed', 'retry_scheduled')
                   ORDER BY jt.created_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
            reclaim_n = conn.execute(
                """SELECT COUNT(*)::int AS n FROM job_traces
                   WHERE event_type = 'lease_reclaimed'"""
            ).fetchone()
            fail_n = conn.execute(
                """SELECT COUNT(*)::int AS n FROM job_traces
                   WHERE event_type = 'failed'"""
            ).fetchone()

        def _clip(row: dict) -> dict:
            return {
                "job_id": row["job_id"],
                "attempt": row["attempt"],
                "runner_id": row.get("runner_id"),
                "goal": ((row.get("goal") or "")[:120]),
                "status": row.get("status"),
                "error": ((row.get("error") or "")[:200]),
                "at": row.get("created_at"),
            }

        return {
            "lease_reclaim_total": int((reclaim_n or {}).get("n") or 0),
            "failed_trace_total": int((fail_n or {}).get("n") or 0),
            "recent_reclaims": [_clip(dict(r)) for r in reclaims],
            "recent_failures": [_clip(dict(r)) for r in fails],
        }
