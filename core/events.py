"""Live event bus — every action streams to the UI in real time."""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class EventBus:
    _instance: EventBus | None = None

    def __init__(self, log_path: Path | None = None):
        self._subscribers: list[Callable[[dict], None]] = []
        self._history: deque[dict] = deque(maxlen=500)
        self._lock = threading.Lock()
        self.log_path = log_path

    @classmethod
    def get(cls, log_path: Path | None = None) -> EventBus:
        if cls._instance is None:
            cls._instance = EventBus(log_path)
        return cls._instance

    def emit(self, event_type: str, data: dict | None = None) -> dict:
        event = {
            "type": event_type,
            "time": datetime.now(timezone.utc).isoformat(),
            **(data or {}),
        }
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(event)
            except Exception:
                pass
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        return event

    def subscribe(self, callback: Callable[[dict], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._history)[-limit:]
