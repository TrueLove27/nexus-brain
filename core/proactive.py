"""Proactive daemon — Nexus works without being poked."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.inbox_retry import InboxRetryTracker
from core.job_queue import DurableJobQueue

FAILURE_STATUSES = frozenset({"failed", "incomplete", "error"})


class ProactiveDaemon:
    def __init__(self, engine, interval: int = 30, on_task_complete: Callable | None = None):
        self.engine = engine
        self.interval = interval
        self.on_task_complete = on_task_complete
        self._running = False
        self._thread: threading.Thread | None = None
        self.log_path = engine.root / "data" / "logs" / "proactive.jsonl"
        data_dir = Path(engine.memory.data_dir)
        proactive_cfg = engine.config.get("proactive") or {}
        self._retry = InboxRetryTracker.from_config(data_dir, proactive_cfg)
        self._queue = DurableJobQueue.from_engine(engine)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _log(self, event: str, data: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _loop(self) -> None:
        self._log("daemon_start", {
            "interval": self.interval,
            "retry_max_attempts": self._retry.max_attempts,
            "retry_base_delay_seconds": self._retry.base_delay_seconds,
            "durable_queue": self._queue is not None,
            "runner_id": self._queue.runner_id if self._queue else None,
        })
        while self._running:
            try:
                self._tick()
            except Exception as e:
                self._log("tick_error", {"error": str(e)})
            time.sleep(self.interval)

    def _handle_result(self, task: str, result: dict) -> None:
        status = (result or {}).get("status") or "failed"
        if status == "done":
            # Successful tasks must not be re-queued.
            cleared = self._retry.clear(task)
            if cleared:
                self._log("retry_cleared", {"goal": task[:200]})
            return

        if status not in FAILURE_STATUSES:
            # Unknown statuses treated as incomplete so they are not lost.
            status = "incomplete"

        error = str((result or {}).get("result") or status)
        outcome = self._retry.record_failure(task, status, error)
        self._log("inbox_retry", {
            "goal": task[:200],
            "status": status,
            **{k: v for k, v in outcome.items() if k != "goal"},
        })

    def _feed_file_inbox(self) -> list[str]:
        """Drain file inbox into durable queue when Postgres is up; else return raw goals."""
        goals = self.engine.memory.get_pending_inbox_tasks()
        if not goals:
            return []
        if self._queue is None:
            return goals
        enqueued = self._queue.ingest_goals(goals, source="inbox")
        self._log("queue_ingest", {
            "file_goals": len(goals),
            "enqueued": len(enqueued),
            "dedup_skipped": len(goals) - len(enqueued),
        })
        return []  # work comes from claim(), not file goals

    def _run_durable_queue(self) -> int:
        """Claim-and-run leased jobs until the queue is idle. Returns jobs processed."""
        assert self._queue is not None
        processed = 0
        while self._running:
            job = self._queue.claim()
            if not job:
                break
            goal = job["goal"]
            job_id = job["id"]
            self._log("queue_claim", {
                "job_id": job_id,
                "goal": goal[:200],
                "attempts": job.get("attempts"),
                "lease_owner": self._queue.runner_id,
            })
            try:
                result = self.engine.run(goal)
            except Exception as e:
                result = {"status": "failed", "result": str(e)}
                self._log("task_error", {"goal": goal[:200], "error": str(e), "job_id": job_id})

            status = (result or {}).get("status") or "failed"
            if status == "done":
                self._queue.complete(job_id, str((result or {}).get("result") or "done"))
                self._retry.clear(goal)
            else:
                err = str((result or {}).get("result") or status)
                # Queue owns retry/backoff via attempts + lease reclaim — no file re-queue.
                updated = self._queue.fail(job_id, err)
                new_status = (updated or {}).get("status")
                self._log("queue_fail", {
                    "job_id": job_id,
                    "status": new_status,
                    "attempts": (updated or {}).get("attempts"),
                    "will_retry": new_status == "pending",
                })
                if new_status == "failed":
                    from brain.inbox import write_failed_task
                    path = write_failed_task(
                        Path(self.engine.memory.data_dir),
                        goal,
                        err,
                        int((updated or {}).get("attempts") or 0),
                    )
                    self._log("queue_exhausted", {"job_id": job_id, "failed_path": str(path)})

            self._log("task_complete", {
                "goal": goal[:200],
                "status": status,
                "job_id": job_id,
                "source": "durable_queue",
            })
            if self.on_task_complete:
                self.on_task_complete(goal, result)
            processed += 1
        return processed

    def _run_file_inbox(self, tasks: list[str]) -> None:
        for task in tasks:
            self._log("inbox_task", {"goal": task[:200]})
            try:
                result = self.engine.run(task)
            except Exception as e:
                result = {"status": "failed", "result": str(e)}
                self._log("task_error", {"goal": task[:200], "error": str(e)})
            self._log("task_complete", {"goal": task[:200], "status": result.get("status")})
            self._handle_result(task, result)
            if self.on_task_complete:
                self.on_task_complete(task, result)

    def _tick(self) -> None:
        for info in self._retry.reclaim_due():
            self._log("retry_reclaimed", info)

        # Prefer durable Postgres queue when available; file inbox feeds it.
        file_goals = self._feed_file_inbox()
        if self._queue is not None:
            n = self._run_durable_queue()
            if n:
                depth = self._queue.depth()
                self._log("queue_depth", depth)
        else:
            self._run_file_inbox(file_goals)

        if self.engine.config.get("proactive", {}).get("scan_on_start"):
            self._sync_portfolio_prompts()

    def _sync_portfolio_prompts(self) -> None:
        try:
            from core.portfolio_bridge import PortfolioBridge
            bridge = PortfolioBridge.from_engine(self.engine)
            dropped = bridge.sync_from_prompts()
            for path in dropped:
                self._log("portfolio_sync", {"file": str(path)})
        except Exception as e:
            self._log("portfolio_sync_error", {"error": str(e)})
