"""Proactive daemon — Nexus works without being poked."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.inbox_retry import InboxRetryTracker

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

    def _tick(self) -> None:
        for info in self._retry.reclaim_due():
            self._log("retry_reclaimed", info)

        tasks = self.engine.memory.get_pending_inbox_tasks()
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
