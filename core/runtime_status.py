"""Read-only runtime status — session progress + inbox task queue."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _tail_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _clip(text: str, n: int = 90) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _is_inbox_noise(name: str, content: str) -> bool:
    if name.lower() in ("readme.txt", "readme.md"):
        return True
    stripped = content.strip()
    if not stripped:
        return True
    # Doc-only files (every non-empty line is a comment)
    body_lines = [ln for ln in stripped.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return not body_lines


def session_progress(root: Path) -> dict[str, Any]:
    """Derive live session state from data/logs/session.jsonl."""
    log_path = root / "data" / "logs" / "session.jsonl"
    events = _tail_jsonl(log_path)

    state = "idle"
    cycles = 0
    completed = 0
    failed = 0
    last_task: str | None = None
    last_status: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

    for ev in events:
        kind = ev.get("event") or ev.get("type") or ""
        if kind == "session_start":
            state = "running"
            cycles = 0
            completed = 0
            failed = 0
            last_task = None
            last_status = None
            started_at = ev.get("time")
            ended_at = None
        elif kind == "task_start":
            state = "running"
            cycles = int(ev.get("cycle") or cycles or 0)
            last_task = (ev.get("task") or "").strip() or last_task
            last_status = "running"
        elif kind == "task_done":
            state = "running"
            cycles = int(ev.get("cycle") or cycles or 0)
            last_status = (ev.get("status") or "unknown").strip()
            if last_status == "done":
                completed += 1
            else:
                failed += 1
        elif kind == "no_tasks":
            pass
        elif kind == "session_end":
            state = "idle"
            cycles = int(ev.get("cycles") or cycles or 0)
            completed = int(ev.get("completed") or completed or 0)
            failed = int(ev.get("failed") or failed or 0)
            ended_at = ev.get("time") or ev.get("ended_at")
            results = ev.get("results") or []
            if results:
                last = results[-1]
                last_task = (last.get("task") or last_task or "").strip() or last_task
                last_status = (last.get("status") or last_status or "").strip() or last_status

    return {
        "state": state,
        "cycles": cycles,
        "completed": completed,
        "failed": failed,
        "last_task": last_task,
        "last_status": last_status,
        "started_at": started_at,
        "ended_at": ended_at,
        "log": str(log_path) if log_path.exists() else None,
    }


def inbox_queue(root: Path, recent_limit: int = 5) -> dict[str, Any]:
    """Peek pending inbox goals and recently processed tasks (non-destructive)."""
    inbox = root / "data" / "inbox"
    pending_items: list[dict[str, str]] = []
    if inbox.exists():
        for path in sorted(inbox.glob("*.txt")):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _is_inbox_noise(path.name, content):
                continue
            # Prefer first non-comment line as the goal preview
            goal = next(
                (ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")),
                content.strip(),
            )
            pending_items.append({"file": path.name, "goal": _clip(goal)})

    processed_items: list[dict[str, str]] = []
    processed = inbox / "processed"
    if processed.exists():
        files = sorted(
            processed.glob("*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:recent_limit]
        for path in files:
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _is_inbox_noise(path.name, content):
                continue
            goal = next(
                (ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")),
                content.strip(),
            )
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            processed_items.append({"file": path.name, "goal": _clip(goal), "at": mtime})

    failed_count = 0
    failed_dir = inbox / "failed"
    if failed_dir.exists():
        failed_count = len(list(failed_dir.glob("*.txt")))

    return {
        "pending": pending_items,
        "pending_count": len(pending_items),
        "recent": processed_items,
        "failed_count": failed_count,
    }


def job_queue_status(health: dict[str, Any] | None) -> dict[str, Any] | None:
    """Surface durable Postgres queue depth from a health payload when present."""
    if not health:
        return None
    if health.get("storage") != "postgres":
        return None
    return {
        "pending": int(health.get("job_queue_pending") or 0),
        "running": int(health.get("job_queue_running") or 0),
        "failed": int(health.get("job_queue_failed") or 0),
        "backend": "postgres",
    }


def build_runtime_status(root: Path, health: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate session + queue (+ optional health fields) for UI /api."""
    session = session_progress(root)
    queue = inbox_queue(root)
    payload: dict[str, Any] = {
        "session": session,
        "queue": queue,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    jq = job_queue_status(health)
    if jq is not None:
        payload["job_queue"] = jq
        # Prefer durable depth for a single top-level queue count when Postgres is live.
        queue = {**queue, "durable_pending": jq["pending"], "durable_running": jq["running"]}
        payload["queue"] = queue
    if health:
        payload["health"] = {
            "ollama": health.get("ollama"),
            "model": health.get("model"),
            "storage": health.get("storage"),
            "inbox_queue": health.get("inbox_queue"),
            "inbox_retries": health.get("inbox_retries"),
            "inbox_failed": health.get("inbox_failed"),
            "job_queue_pending": health.get("job_queue_pending"),
            "job_queue_running": health.get("job_queue_running"),
            "job_queue_failed": health.get("job_queue_failed"),
            "portfolio_pending": health.get("portfolio_pending"),
            "portfolio_done": health.get("portfolio_done"),
        }
    return payload