"""Timed portfolio work session — runs tasks for a fixed duration."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
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

    def _write_summary_report(self, summary: dict) -> dict[str, Path]:
        """Write human-readable and structured session summary under data/logs/."""
        logs_dir = self.log_path.parent
        logs_dir.mkdir(parents=True, exist_ok=True)

        started_at = summary["started_at"]
        ended_at = summary["ended_at"]
        stamp = datetime.fromisoformat(ended_at).strftime("%Y%m%d_%H%M%S")
        md_path = logs_dir / f"session_summary_{stamp}.md"
        json_path = logs_dir / f"session_summary_{stamp}.json"

        duration_seconds = summary["duration_seconds"]
        hours, rem = divmod(int(duration_seconds), 3600)
        minutes, seconds = divmod(rem, 60)
        duration_label = f"{hours}h {minutes}m {seconds}s"

        completed = summary["completed"]
        failed = summary["failed"]
        cycles = summary["cycles"]
        results = summary["results"]

        report = {
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "duration": duration_label,
            "hours_configured": self.hours,
            "cycles": cycles,
            "completed": completed,
            "failed": failed,
            "results": results,
            "session_jsonl": str(self.log_path),
        }

        lines = [
            "# Session Summary",
            "",
            f"- **Started:** {started_at}",
            f"- **Ended:** {ended_at}",
            f"- **Duration:** {duration_label} ({duration_seconds:.1f}s)",
            f"- **Configured hours:** {self.hours}",
            f"- **Cycles attempted:** {cycles}",
            f"- **Successes:** {completed}",
            f"- **Failures:** {failed}",
            f"- **JSONL log:** `{self.log_path}`",
            "",
            "## Tasks",
            "",
        ]
        if not results:
            lines.append("_No tasks were run._")
        else:
            for r in results:
                status = r.get("status", "unknown")
                task = (r.get("task") or "").strip() or "(unnamed)"
                cycle = r.get("cycle", "?")
                lines.append(f"- Cycle {cycle}: **{status}** — {task}")
        lines.append("")

        md_path.write_text("\n".join(lines), encoding="utf-8")
        json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        return {"md": md_path, "json": json_path}

    def run(self, on_task: Callable[[str], None] | None = None) -> dict:
        from core.portfolio_bridge import PortfolioBridge

        # Mark-done + state bump happen inside engine.run() via on_task_success.
        bridge = PortfolioBridge.from_engine(self.engine)

        started_at = datetime.now(timezone.utc)
        end = time.time() + self.hours * 3600
        cycles = 0
        results: list[dict] = []

        self._log("session_start", {"hours": self.hours, "pending": bridge.pending_count()})
        bus = getattr(self.engine, "bus", None)
        if bus is not None:
            bus.emit("session_start", {"hours": self.hours, "pending": bridge.pending_count()})

        while time.time() < end:
            cycles += 1
            dropped = bridge.drop_next_task(str(self.engine.root))
            if not dropped:
                self._log("no_tasks")
                break

            task_text = bridge.next_unchecked_task() or "portfolio task"
            self._log("task_start", {"cycle": cycles, "task": task_text[:200]})
            if bus is not None:
                bus.emit("session_task_start", {"cycle": cycles, "task": task_text[:200]})

            if on_task:
                on_task(task_text)

            result = self.engine.run(task_text)
            status = result.get("status", "unknown")
            results.append({"cycle": cycles, "task": task_text, "status": status})
            self._log("task_done", {"cycle": cycles, "status": status})
            if bus is not None:
                bus.emit("session_task_done", {"cycle": cycles, "status": status, "task": task_text[:200]})

            remaining = end - time.time()
            if remaining <= 0:
                break
            sleep_for = min(self.interval_seconds, remaining)
            time.sleep(sleep_for)

        ended_at = datetime.now(timezone.utc)
        completed = sum(1 for r in results if r["status"] == "done")
        failed = sum(1 for r in results if r["status"] != "done")
        summary = {
            "cycles": cycles,
            "completed": completed,
            "failed": failed,
            "results": results,
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": (ended_at - started_at).total_seconds(),
        }
        self._log("session_end", summary)

        paths = self._write_summary_report(summary)
        summary["summary_md"] = str(paths["md"])
        summary["summary_json"] = str(paths["json"])

        if bus is not None:
            bus.emit(
                "session_summary",
                {
                    "cycles": cycles,
                    "completed": completed,
                    "failed": failed,
                    "summary_md": summary["summary_md"],
                    "summary_json": summary["summary_json"],
                },
            )

        return summary
