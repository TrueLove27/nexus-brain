"""Bridge between portfolio-growth-engine and nexus-brain inbox."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.engine import NexusEngine


DEFAULT_ENGINE_ROOT = Path(r"C:\Users\cheki\Desktop\New folder (2)\portfolio-growth-engine")
PORTFOLIO_HEADER = "PORTFOLIO SESSION TASK"


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

    @classmethod
    def from_engine(cls, engine: "NexusEngine") -> "PortfolioBridge":
        pg = engine.config.get("portfolio", {}) or {}
        return cls(
            engine_root=pg.get("engine_root") or DEFAULT_ENGINE_ROOT,
            inbox_dir=engine.root / "data" / "inbox",
            project=pg.get("project", "nexus-brain"),
        )

    @property
    def task_file(self) -> Path:
        return self.engine_root / "tasks" / f"{self.project}.md"

    @property
    def prompts_dir(self) -> Path:
        return self.engine_root / "prompts"

    @property
    def state_file(self) -> Path:
        return self.engine_root / "state.json"

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

    @staticmethod
    def parse_portfolio_goal(goal: str) -> tuple[str | None, str | None]:
        """Return (project_slug, task_text) from an inbox body, or (None, None)."""
        if PORTFOLIO_HEADER not in goal:
            return None, None
        proj = re.search(r"^Project:\s*TrueLove27/(\S+)", goal, re.M)
        task = re.search(r"^Task:\s*(.+)$", goal, re.M)
        return (
            proj.group(1) if proj else None,
            task.group(1).strip() if task else None,
        )

    def resolve_task_text(self, goal: str) -> str | None:
        """Map any goal string (inbox body or bare task) to a backlog task line."""
        project, parsed = self.parse_portfolio_goal(goal)
        if project:
            self.project = project
        if parsed:
            return parsed

        task = goal.strip()
        if not self.task_file.exists():
            return None
        for line in self.task_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("- [ ]") and task.lower() in line.lower():
                return line.replace("- [ ]", "", 1).strip()
            if line.startswith("- [ ]") and line.lower().replace("- [ ]", "", 1).strip() == task.lower():
                return task
        # Exact bare match against unchecked line text
        for line in self.task_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("- [ ]"):
                bare = line.replace("- [ ]", "", 1).strip()
                if bare.lower() == task.lower() or task.lower() in bare.lower():
                    return bare
        return None

    def bump_state(self) -> None:
        """Increment improvements_on_current and refresh last_run in state.json."""
        if not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        state["improvements_on_current"] = int(state.get("improvements_on_current", 0)) + 1
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        state["active_project"] = self.project
        self.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def on_task_success(self, goal: str) -> bool:
        """After a successful run: mark backlog checkbox and bump state.

        Idempotent — already-checked items return False and do not bump state.
        """
        task_text = self.resolve_task_text(goal)
        if not task_text:
            return False
        marked = self.mark_task_done(task_text)
        if marked:
            self.bump_state()
        return marked

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
