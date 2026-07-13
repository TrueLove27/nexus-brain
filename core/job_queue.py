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
    ):
        self.repo = repo
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
        )

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
        return self.repo.claim(self.runner_id, lease_seconds=self.lease_seconds)

    def heartbeat(self, job_id: int) -> bool:
        return self.repo.heartbeat(job_id, self.runner_id, lease_seconds=self.lease_seconds)

    def complete(self, job_id: int, result: str = "") -> bool:
        return self.repo.complete(job_id, self.runner_id, result)

    def fail(self, job_id: int, error: str = "", *, retry: bool | None = None) -> dict[str, Any] | None:
        return self.repo.fail(
            job_id,
            self.runner_id,
            error,
            retry=retry,
            retry_base_seconds=self.retry_base_seconds,
            retry_backoff=self.retry_backoff,
        )

    def cancel(self, job_id: int, reason: str = "cancelled") -> bool:
        return self.repo.cancel(job_id, reason)

    def depth(self) -> dict[str, int]:
        return self.repo.depth()

    def list_pending(self, limit: int = 8) -> list[dict[str, Any]]:
        return self.repo.list_recent(limit=limit, status="pending")
