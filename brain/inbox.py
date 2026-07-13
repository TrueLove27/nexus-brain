"""Inbox file helpers — pickup, failed archive, retry restore."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path


def inbox_dir(data_dir: Path) -> Path:
    path = data_dir / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def processed_dir(data_dir: Path) -> Path:
    path = inbox_dir(data_dir) / "processed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def failed_dir(data_dir: Path) -> Path:
    path = inbox_dir(data_dir) / "failed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_key(goal: str) -> str:
    return hashlib.sha256(goal.strip().encode("utf-8")).hexdigest()[:16]


def get_pending_inbox_tasks(data_dir: Path) -> list[str]:
    """Read pending .txt goals from inbox root and move them to processed/."""
    root = inbox_dir(data_dir)
    tasks: list[str] = []
    dest = processed_dir(data_dir)
    for f in sorted(root.glob("*.txt")):
        content = f.read_text(encoding="utf-8").strip()
        if not content:
            f.rename(dest / f.name)
            continue
        tasks.append(content)
        target = dest / f.name
        if target.exists():
            stem, suffix = f.stem, f.suffix
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            target = dest / f"{stem}_{stamp}{suffix}"
        f.rename(target)
    return tasks


def write_failed_task(data_dir: Path, goal: str, error: str, attempts: int) -> Path:
    """Archive a permanently failed task under data/inbox/failed/."""
    key = task_key(goal)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = failed_dir(data_dir) / f"{stamp}_{key}.txt"
    body = (
        f"# FAILED after {attempts} attempt(s)\n"
        f"# error: {error[:500]}\n"
        f"# archived: {datetime.now(timezone.utc).isoformat()}\n"
        f"\n{goal.strip()}\n"
    )
    out.write_text(body, encoding="utf-8")
    return out


def restore_task_to_inbox(data_dir: Path, goal: str, attempt: int) -> Path | None:
    """Re-queue a goal into the inbox if not already pending (same content)."""
    root = inbox_dir(data_dir)
    goal = goal.strip()
    for existing in root.glob("*.txt"):
        if existing.read_text(encoding="utf-8").strip() == goal:
            return None
    key = task_key(goal)
    out = root / f"retry_{key}_a{attempt}.txt"
    out.write_text(goal + "\n", encoding="utf-8")
    return out
