"""Idempotent tool-call effect ledger repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from core.secret_redact import redact_string, redact_structure


class ToolEffectRepo:
    def __init__(self, conn_factory):
        self._conn = conn_factory

    def get(self, job_id: int, effect_key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, job_id, effect_key, action, args, result, iteration,
                          fence_token, runner_id, status, skipped_count, created_at, updated_at
                   FROM tool_call_effects
                   WHERE job_id = %s AND effect_key = %s""",
                (int(job_id), effect_key),
            ).fetchone()
        return dict(row) if row else None

    def record_applied(
        self,
        job_id: int,
        effect_key: str,
        *,
        action: str,
        args: dict | None = None,
        result: str = "",
        iteration: int | None = None,
        fence_token: int | None = None,
        runner_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Insert-or-keep an applied effect. Existing applied rows win (idempotent)."""
        now = datetime.now(timezone.utc)
        safe_args = redact_structure(args or {})
        safe_result = redact_string(str(result or ""))[:4000]
        safe_action = redact_string(str(action or ""))
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO tool_call_effects
                   (job_id, effect_key, action, args, result, iteration,
                    fence_token, runner_id, status, skipped_count, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'applied', 0, %s, %s)
                   ON CONFLICT (job_id, effect_key) DO UPDATE SET
                     updated_at = EXCLUDED.updated_at
                   WHERE tool_call_effects.status <> 'applied'
                   RETURNING *""",
                (
                    int(job_id),
                    effect_key,
                    safe_action,
                    json.dumps(safe_args),
                    safe_result,
                    iteration,
                    fence_token,
                    runner_id,
                    now,
                    now,
                ),
            ).fetchone()
            if row is None:
                # Already applied — return the durable winner.
                row = conn.execute(
                    """SELECT id, job_id, effect_key, action, args, result, iteration,
                              fence_token, runner_id, status, skipped_count, created_at, updated_at
                       FROM tool_call_effects
                       WHERE job_id = %s AND effect_key = %s""",
                    (int(job_id), effect_key),
                ).fetchone()
            conn.commit()
        return dict(row) if row else None

    def mark_skipped(self, job_id: int, effect_key: str) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            conn.execute(
                """UPDATE tool_call_effects
                   SET skipped_count = skipped_count + 1, updated_at = %s
                   WHERE job_id = %s AND effect_key = %s AND status = 'applied'""",
                (now, int(job_id), effect_key),
            )
            conn.commit()

    def seed_from_steps(self, job_id: int, steps: list[dict[str, Any]]) -> int:
        """Upsert prior checkpoint steps; returns count of newly inserted rows."""
        if not steps:
            return 0
        now = datetime.now(timezone.utc)
        inserted = 0
        with self._conn() as conn:
            for step in steps:
                key = step.get("effect_key")
                action = step.get("action")
                if not key or not action:
                    continue
                safe_args = redact_structure(step.get("args") or {})
                safe_result = redact_string(str(step.get("result") or ""))[:4000]
                row = conn.execute(
                    """INSERT INTO tool_call_effects
                       (job_id, effect_key, action, args, result, iteration,
                        fence_token, runner_id, status, skipped_count, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, 'applied', 0, %s, %s)
                       ON CONFLICT (job_id, effect_key) DO NOTHING
                       RETURNING id""",
                    (
                        int(job_id),
                        key,
                        redact_string(str(action)),
                        json.dumps(safe_args),
                        safe_result,
                        step.get("iteration"),
                        now,
                        now,
                    ),
                ).fetchone()
                if row:
                    inserted += 1
            conn.commit()
        return inserted

    def list_for_job(self, job_id: int, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, job_id, effect_key, action, args, result, iteration,
                          fence_token, runner_id, status, skipped_count, created_at, updated_at
                   FROM tool_call_effects
                   WHERE job_id = %s
                   ORDER BY created_at ASC, id ASC
                   LIMIT %s""",
                (int(job_id), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_for_job(self, job_id: int) -> dict[str, int]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT
                     COUNT(*)::int AS total,
                     COALESCE(SUM(skipped_count), 0)::int AS skips
                   FROM tool_call_effects WHERE job_id = %s""",
                (int(job_id),),
            ).fetchone()
        if not row:
            return {"total": 0, "skips": 0}
        return {"total": int(row["total"] or 0), "skips": int(row["skips"] or 0)}
