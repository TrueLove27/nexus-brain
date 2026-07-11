"""Bridge between portfolio-growth-engine and nexus-brain inbox."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ENGINE_ROOT = Path(r"C:\Users\cheki\Desktop\New folder (2)\portfolio-growth-engine")


class PortfolioBridge:
    def __init__(
        self,
        engine_root: Path | str | None = None,
        inbox_dir: Path | None = None,
        project: str = "nexus-brain",
    ):
        self.engine_root = Path(engine_root) if engine_root else DEFAULT_ENGINE_ROOT
        self.inbox_dir = inbox_dir or Path("data/inbox")
        self.project = project
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    @property
    def task_file(self) -> Path:
        return self.engine_root / "tasks" / f"{self.project}.md"

    @property
    def prompts_dir(self) -> Path:
        return self.engine_root / "prompts"

    def next_unchecked_task(self) -> str | None:
        if not self.task_file.exists():
            return None
        for line in self.task_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("- [ ]"):
                return line.replace("- [ ]", "", 1).strip()
        return None

    def mark_task_done(self, task_text: str) -> bool:
        if not self.task_file.exists():
            return False
        lines = self.task_file.read_text(encoding="utf-8").splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith("- [ ]") and task_text.lower() in line.lower():
                lines[i] = line.replace("- [ ]", "- [x]", 1)
                updated = True
                break
        if updated:
            self.task_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return updated

    def drop_next_task(self, local_repo_path: str) -> Path | None:
        task = self.next_unchecked_task()
        if not task:
            return None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = self.inbox_dir / f"portfolio_{stamp}.txt"
        body = f"""PORTFOLIO SESSION TASK
Project: TrueLove27/{self.project}
Task: {task}

Instructions:
1. Implement this improvement in {local_repo_path}
2. Match existing code style and architecture
3. Commit with a descriptive message and push to origin
4. After success, mark done in portfolio-growth-engine/tasks/{self.project}.md
"""
        out.write_text(body, encoding="utf-8")
        return out

    def sync_from_prompts(self) -> list[Path]:
        """Watch prompts/today.md and drop updated tasks into inbox."""
        prompt_file = self.prompts_dir / "today.md"
        if not prompt_file.exists():
            return []

        content = prompt_file.read_text(encoding="utf-8")
        match = re.search(r"(?ms)## Task\s*\n(.+?)\n\n##", content)
        if not match:
            return []

        task = match.group(1).strip()
        stamp = prompt_file.stat().st_mtime
        marker = self.inbox_dir / ".last_prompt_sync"
        last = float(marker.read_text()) if marker.exists() else 0.0
        if stamp <= last:
            return []

        path = self.drop_next_task("")  # re-use task from backlog
        if path:
            marker.write_text(str(stamp))
            return [path]
        return []

    def pending_count(self) -> int:
        if not self.task_file.exists():
            return 0
        return sum(1 for line in self.task_file.read_text(encoding="utf-8").splitlines()
                   if line.startswith("- [ ]"))

    def completed_count(self) -> int:
        if not self.task_file.exists():
            return 0
        return sum(1 for line in self.task_file.read_text(encoding="utf-8").splitlines()
                   if line.startswith("- [x]"))
