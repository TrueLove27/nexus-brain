"""Durable Postgres job queue — enqueue, lease/claim, status transitions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_RETRY_BASE_SECONDS = 30
DEFAULT_RETRY_BACKOFF = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _retry_delay(attempts: int, base: int = DEFAULT_RETRY_BASE_SECONDS, mult: float = DEFAULT_RETRY_BACKOFF) -> int:
    exp = max(0, attempts - 1)
    return int(max(1, base) * (mult ** exp))


class JobQueueRepo:
    """Low-level queue ops. Single-runner via FOR UPDATE SKIP LOCKED + lease expiry."""

    def __init__(self, conn_factory):
        self._conn = conn_factory

    def enqueue(
        self,
        goal: str,
        *,
        dedup_key: str | None = None,
        source: str = "inbox",
        payload: dict | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> dict[str, Any] | None:
        """Insert a pending job. Returns None if an active dedup collision occurs."""
        goal = (goal or "").strip()
        if not goal:
            return None
        now = _now()
        with self._conn() as conn:
            try:
                row = conn.execute(
                    """INSERT INTO jobs
                       (goal, status, dedup_key, source, payload, max_attempts,
                        available_at, created_at, updated_at)
                       VALUES (%s, 'pending', %s, %s, %s, %s, %s, %s, %s)
                       RETURNING *""",
                    (
                        goal,
                        dedup_key,
                        source,
                        json.dumps(payload or {}),
                        max(1, int(max_attempts)),
                        now,
                        now,
                        now,
                    ),
                ).fetchone()
                conn.commit()
            except Exception as exc:
                conn.rollback()
                # Unique violation on active dedup — treat as intentional no-op
                sqlstate = getattr(exc, "sqlstate", None)
                if sqlstate == "23505" or "idx_jobs_dedup_active" in str(exc) or "UniqueViolation" in type(exc).__name__:
                    return None
                raise
        return dict(row) if row else None

    def claim(
        self,
        runner_id: str,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        """
        Atomically claim one claimable job (pending and available, or expired lease).
        Concurrent workers use SKIP LOCKED so only one runner wins each row.
        """
        now = _now()
        lease_exp = now + timedelta(seconds=max(30, int(lease_seconds)))
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE jobs SET
                    status = 'running',
                    lease_owner = %s,
                    lease_expires_at = %s,
                    attempts = attempts + 1,
                    started_at = COALESCE(started_at, %s),
                    updated_at = %s,
                    error = CASE
                        WHEN status = 'running' THEN COALESCE(error, 'lease_reclaimed')
                        ELSE error
                    END
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE (
                        status = 'pending' AND available_at <= %s
                    ) OR (
                        status = 'running'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at < %s
                    )
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING *
                """,
                (runner_id, lease_exp, now, now, now, now),
            ).fetchone()
            conn.commit()
        return dict(row) if row else None

    def heartbeat(
        self,
        job_id: int,
        runner_id: str,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> bool:
        """Extend lease if this runner still owns the job."""
        now = _now()
        lease_exp = now + timedelta(seconds=max(30, int(lease_seconds)))
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE jobs SET lease_expires_at = %s, updated_at = %s
                   WHERE id = %s AND status = 'running' AND lease_owner = %s""",
                (lease_exp, now, job_id, runner_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def complete(self, job_id: int, runner_id: str, result: str = "") -> bool:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE jobs SET
                       status = 'done',
                       result = %s,
                       lease_owner = NULL,
                       lease_expires_at = NULL,
                       completed_at = %s,
                       updated_at = %s
                   WHERE id = %s AND status = 'running' AND lease_owner = %s""",
                ((result or "")[:8000], now, now, job_id, runner_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def fail(
        self,
        job_id: int,
        runner_id: str,
        error: str = "",
        *,
        retry: bool | None = None,
        retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
    ) -> dict[str, Any] | None:
        """
        Mark job failed, or re-queue as pending when attempts remain.
        Returns updated job row, or None if ownership mismatch.
        """
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = %s AND status = 'running' AND lease_owner = %s",
                (job_id, runner_id),
            ).fetchone()
            if not row:
                conn.commit()
                return None

            attempts = int(row["attempts"] or 0)
            max_attempts = int(row["max_attempts"] or DEFAULT_MAX_ATTEMPTS)
            should_retry = retry if retry is not None else attempts < max_attempts
            err = (error or "failed")[:2000]

            if should_retry:
                delay = _retry_delay(attempts, retry_base_seconds, retry_backoff)
                available = now + timedelta(seconds=delay)
                updated = conn.execute(
                    """UPDATE jobs SET
                           status = 'pending',
                           error = %s,
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           available_at = %s,
                           updated_at = %s
                       WHERE id = %s
                       RETURNING *""",
                    (err, available, now, job_id),
                ).fetchone()
            else:
                updated = conn.execute(
                    """UPDATE jobs SET
                           status = 'failed',
                           error = %s,
                           result = %s,
                           lease_owner = NULL,
                           lease_expires_at = NULL,
                           completed_at = %s,
                           updated_at = %s
                       WHERE id = %s
                       RETURNING *""",
                    (err, err, now, now, job_id),
                ).fetchone()
            conn.commit()
        return dict(updated) if updated else None

    def cancel(self, job_id: int, reason: str = "cancelled") -> bool:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE jobs SET
                       status = 'cancelled',
                       error = %s,
                       lease_owner = NULL,
                       lease_expires_at = NULL,
                       completed_at = %s,
                       updated_at = %s
                   WHERE id = %s AND status IN ('pending', 'running')""",
                ((reason or "cancelled")[:2000], now, now, job_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def depth(self) -> dict[str, int]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT status, COUNT(*)::int AS n FROM jobs
                   GROUP BY status"""
            ).fetchall()
        counts = {r["status"]: int(r["n"]) for r in rows}
        return {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "done": counts.get("done", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
        }

    def list_recent(self, limit: int = 10, status: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    """SELECT id, goal, status, dedup_key, source, attempts, error,
                              lease_owner, lease_expires_at, available_at, created_at, completed_at
                       FROM jobs WHERE status = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, goal, status, dedup_key, source, attempts, error,
                              lease_owner, lease_expires_at, available_at, created_at, completed_at
                       FROM jobs ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get(self, job_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()
        return dict(row) if row else None
