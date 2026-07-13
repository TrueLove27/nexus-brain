"""Durable job queue facade — Postgres when available, file-inbox feeder otherwise."""

from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING, Any

from brain.inbox import task_key
from brain.repos.job_queue import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_RETRY_BASE_SECONDS,
    JobQueueRepo,
)

if TYPE_CHECKING:
    from core.engine import NexusEngine


def default_runner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class DurableJobQueue:
    """High-level queue: ingest file inbox → claim with lease → complete/fail."""

    def __init__(
        self,
        repo: JobQueueRepo,
        *,
        runner_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
        retry_backoff: float = DEFAULT_RETRY_BACKOFF,
        traces=None,
    ):
        self.repo = repo
        self.traces = traces
        self.runner_id = runner_id or default_runner_id()
        self.lease_seconds = max(30, int(lease_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.retry_base_seconds = max(1, int(retry_base_seconds))
        self.retry_backoff = float(retry_backoff)

    @classmethod
    def from_engine(cls, engine: "NexusEngine") -> "DurableJobQueue | None":
        """Return a queue bound to PostgresMemory, or None if unavailable."""
        mem = getattr(engine, "memory", None)
        if mem is None or getattr(mem, "storage_type", None) != "postgres":
            return None
        repo = getattr(mem, "job_queue", None)
        if repo is None:
            return None
        cfg = (engine.config.get("brain") or {}).get("job_queue") or {}
        proactive = engine.config.get("proactive") or {}
        retry_cfg = proactive.get("retry") or {}
        return cls(
            repo,
            lease_seconds=cfg.get("lease_seconds", DEFAULT_LEASE_SECONDS),
            max_attempts=retry_cfg.get("max_attempts", cfg.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
            retry_base_seconds=retry_cfg.get(
                "base_delay_seconds", cfg.get("retry_base_seconds", DEFAULT_RETRY_BASE_SECONDS)
            ),
            retry_backoff=retry_cfg.get(
                "backoff_multiplier", cfg.get("retry_backoff", DEFAULT_RETRY_BACKOFF)
            ),
            traces=getattr(mem, "job_traces", None),
        )

    def _trace(
        self,
        job_id: int,
        event_type: str,
        *,
        attempt: int = 1,
        task_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        if self.traces is None:
            return
        try:
            self.traces.record(
                job_id,
                event_type,
                attempt=attempt,
                runner_id=self.runner_id,
                task_id=task_id,
                payload=payload,
            )
        except Exception:
            pass

    def enqueue(
        self,
        goal: str,
        *,
        dedup_key: str | None = None,
        source: str = "inbox",
        payload: dict | None = None,
    ) -> dict[str, Any] | None:
        key = dedup_key if dedup_key is not None else task_key(goal)
        return self.repo.enqueue(
            goal,
            dedup_key=key,
            source=source,
            payload=payload,
            max_attempts=self.max_attempts,
        )

    def ingest_goals(self, goals: list[str], *, source: str = "inbox") -> list[dict[str, Any]]:
        """Enqueue drained file-inbox goals; skips active dedup collisions."""
        enqueued: list[dict[str, Any]] = []
        for goal in goals:
            job = self.enqueue(goal, source=source)
            if job:
                enqueued.append(job)
        return enqueued

    def claim(self) -> dict[str, Any] | None:
        job = self.repo.claim(self.runner_id, lease_seconds=self.lease_seconds)
        if not job:
            return None
        job_id = int(job["id"])
        attempt = int(job.get("attempts") or 1)
        if job.get("lease_reclaimed"):
            self._trace(
                job_id,
                "lease_reclaimed",
                attempt=attempt,
                payload={
                    "prev_owner": job.get("prev_owner"),
                    "prev_lease_expires_at": str(job.get("prev_lease_expires_at") or ""),
                    "new_owner": self.runner_id,
                    "goal": (job.get("goal") or "")[:200],
                },
            )
        self._trace(
            job_id,
            "claimed",
            attempt=attempt,
            payload={
                "goal": (job.get("goal") or "")[:200],
                "reclaimed": bool(job.get("lease_reclaimed")),
            },
        )
        return job

    def heartbeat(self, job_id: int) -> bool:
        return self.repo.heartbeat(job_id, self.runner_id, lease_seconds=self.lease_seconds)

    def complete(
        self,
        job_id: int,
        result: str = "",
        *,
        task_id: int | None = None,
        attempt: int = 1,
    ) -> bool:
        ok = self.repo.complete(job_id, self.runner_id, result)
        if ok:
            self._trace(
                job_id,
                "completed",
                attempt=attempt,
                task_id=task_id,
                payload={"result": (result or "")[:500]},
            )
        return ok

    def fail(
        self,
        job_id: int,
        error: str = "",
        *,
        retry: bool | None = None,
        task_id: int | None = None,
        attempt: int = 1,
    ) -> dict[str, Any] | None:
        updated = self.repo.fail(
            job_id,
            self.runner_id,
            error,
            retry=retry,
            retry_base_seconds=self.retry_base_seconds,
            retry_backoff=self.retry_backoff,
        )
        if updated:
            new_status = updated.get("status")
            event = "retry_scheduled" if new_status == "pending" else "failed"
            self._trace(
                job_id,
                event,
                attempt=int(updated.get("attempts") or attempt),
                task_id=task_id,
                payload={
                    "error": (error or "")[:500],
                    "status": new_status,
                    "available_at": str(updated.get("available_at") or ""),
                },
            )
        return updated

    def cancel(self, job_id: int, reason: str = "cancelled") -> bool:
        ok = self.repo.cancel(job_id, reason)
        if ok:
            self._trace(job_id, "cancelled", payload={"reason": (reason or "")[:500]})
        return ok

    def depth(self) -> dict[str, int]:
        return self.repo.depth()

    def list_pending(self, limit: int = 8) -> list[dict[str, Any]]:
        return self.repo.list_recent(limit=limit, status="pending")

    def forensics(self, job_id: int) -> dict[str, Any] | None:
        if self.traces is None:
            return None
        return self.traces.forensics(job_id)
