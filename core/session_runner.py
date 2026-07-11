"""Timed portfolio work session — runs tasks for a fixed duration."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Callable


class SessionRunner:
    def __init__(self, engine, hours: float = 3.0, interval_minutes: int = 55):
        self.engine = engine
        self.hours = hours
        self.interval_seconds = interval_minutes * 60
        self.log_path = engine.root / "data" / "logs" / "session.jsonl"

    def _log(self, event: str, data: dict | None = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **(data or {}),
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def run(self, on_task: Callable[[str], None] | None = None) -> dict:
        from core.portfolio_bridge import PortfolioBridge

        bridge = PortfolioBridge(
            inbox_dir=self.engine.root / "data" / "inbox",
            project="nexus-brain",
        )

        end = time.time() + self.hours * 3600
        cycles = 0
        results: list[dict] = []

        self._log("session_start", {"hours": self.hours, "pending": bridge.pending_count()})

        while time.time() < end:
            cycles += 1
            dropped = bridge.drop_next_task(str(self.engine.root))
            if not dropped:
                self._log("no_tasks")
                break

            task_text = bridge.next_unchecked_task() or "portfolio task"
            self._log("task_start", {"cycle": cycles, "task": task_text[:200]})

            if on_task:
                on_task(task_text)

            result = self.engine.run(task_text)
            status = result.get("status", "unknown")
            results.append({"cycle": cycles, "task": task_text, "status": status})
            self._log("task_done", {"cycle": cycles, "status": status})

            if status == "done":
                bridge.mark_task_done(task_text)

            remaining = end - time.time()
            if remaining <= 0:
                break
            sleep_for = min(self.interval_seconds, remaining)
            time.sleep(sleep_for)

        summary = {
            "cycles": cycles,
            "completed": sum(1 for r in results if r["status"] == "done"),
            "results": results,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
        self._log("session_end", summary)
        return summary
