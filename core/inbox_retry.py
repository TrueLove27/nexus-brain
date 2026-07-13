"""Persistent retry state for failed/incomplete inbox tasks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from brain.inbox import restore_task_to_inbox, task_key, write_failed_task


DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY_SECONDS = 30
DEFAULT_BACKOFF_MULTIPLIER = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class InboxRetryTracker:
    """Exponential backoff for inbox tasks; state survives daemon restarts."""

    def __init__(
        self,
        data_dir: Path,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_seconds: int = DEFAULT_BASE_DELAY_SECONDS,
        backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
    ):
        self.data_dir = Path(data_dir)
        self.max_attempts = max(1, int(max_attempts))
        self.base_delay_seconds = max(1, int(base_delay_seconds))
        self.backoff_multiplier = float(backoff_multiplier)
        self.state_path = self.data_dir / "inbox" / "retries.json"
        self._state: dict[str, Any] = {"tasks": {}}
        self._load()

    @classmethod
    def from_config(cls, data_dir: Path, config: dict | None) -> "InboxRetryTracker":
        cfg = (config or {}).get("retry") or {}
        return cls(
            data_dir=data_dir,
            max_attempts=cfg.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
            base_delay_seconds=cfg.get("base_delay_seconds", DEFAULT_BASE_DELAY_SECONDS),
            backoff_multiplier=cfg.get("backoff_multiplier", DEFAULT_BACKOFF_MULTIPLIER),
        )

    def _load(self) -> None:
        if not self.state_path.exists():
            self._state = {"tasks": {}}
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
            tasks = raw.get("tasks")
            if not isinstance(tasks, dict):
                tasks = {}
            self._state = {"tasks": tasks}
        except (json.JSONDecodeError, OSError):
            self._state = {"tasks": {}}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": _now().isoformat(),
            "max_attempts": self.max_attempts,
            "base_delay_seconds": self.base_delay_seconds,
            "backoff_multiplier": self.backoff_multiplier,
            "tasks": self._state.get("tasks", {}),
        }
        self.state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _delay_for_attempt(self, attempts: int) -> int:
        # after 1st failure: base; after 2nd: base*mult; after 3rd: base*mult^2 …
        exp = max(0, attempts - 1)
        return int(self.base_delay_seconds * (self.backoff_multiplier ** exp))

    def clear(self, goal: str) -> bool:
        """Drop retry state after a successful completion (no re-queue)."""
        key = task_key(goal)
        tasks = self._state.setdefault("tasks", {})
        if key not in tasks:
            return False
        del tasks[key]
        self._save()
        return True

    def record_failure(self, goal: str, status: str, error: str = "") -> dict[str, Any]:
        """
        Record a failed/incomplete run.

        Returns a dict with keys: action ('scheduled'|'exhausted'), attempts,
        next_attempt_at (if scheduled), failed_path (if exhausted).
        """
        key = task_key(goal)
        now = _now()
        tasks = self._state.setdefault("tasks", {})
        entry = tasks.get(key) or {
            "goal": goal.strip(),
            "attempts": 0,
            "created_at": now.isoformat(),
        }
        entry["goal"] = goal.strip()
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_status"] = status
        entry["last_error"] = (error or status)[:500]
        entry["updated_at"] = now.isoformat()

        attempts = entry["attempts"]
        if attempts >= self.max_attempts:
            failed_path = write_failed_task(
                self.data_dir, goal, entry["last_error"], attempts,
            )
            if key in tasks:
                del tasks[key]
            self._save()
            return {
                "action": "exhausted",
                "attempts": attempts,
                "failed_path": str(failed_path),
                "key": key,
            }

        delay = self._delay_for_attempt(attempts)
        next_at = now + timedelta(seconds=delay)
        entry["next_attempt_at"] = next_at.isoformat()
        entry["next_delay_seconds"] = delay
        tasks[key] = entry
        self._save()
        return {
            "action": "scheduled",
            "attempts": attempts,
            "next_attempt_at": entry["next_attempt_at"],
            "next_delay_seconds": delay,
            "key": key,
        }

    def reclaim_due(self) -> list[dict[str, Any]]:
        """Re-queue tasks whose backoff window has elapsed. Returns reclaim summaries."""
        now = _now()
        tasks = self._state.setdefault("tasks", {})
        reclaimed: list[dict[str, Any]] = []
        for key, entry in list(tasks.items()):
            next_at_raw = entry.get("next_attempt_at")
            if not next_at_raw:
                continue
            try:
                next_at = _parse_iso(next_at_raw)
            except ValueError:
                next_at = now
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            if next_at > now:
                continue
            goal = entry.get("goal") or ""
            if not goal.strip():
                del tasks[key]
                continue
            path = restore_task_to_inbox(
                self.data_dir, goal, int(entry.get("attempts", 0)) + 1,
            )
            # Clear next_attempt_at so we don't reclaim again until next failure.
            entry["next_attempt_at"] = None
            entry["reclaimed_at"] = now.isoformat()
            tasks[key] = entry
            reclaimed.append({
                "key": key,
                "goal": goal[:200],
                "attempts": entry.get("attempts", 0),
                "path": str(path) if path else None,
                "skipped_duplicate": path is None,
            })
        if reclaimed:
            self._save()
        return reclaimed

    def pending_count(self) -> int:
        return len(self._state.get("tasks") or {})
