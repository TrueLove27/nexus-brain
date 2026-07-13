"""Nexus execution engine — ties brain, agents, and tools together."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agents.factory import AgentFactory
from agents.orchestrator import OrchestratorAgent
from brain.learning import BrainLearning
from brain.personality import BrainPersonality
from brain.store import MemoryBackend, create_memory
from llm.ollama import OllamaProvider
from tools.registry import ToolRegistry
from core.events import EventBus


class NexusEngine:
    def __init__(self, config_path: Path | None = None):
        root = Path(__file__).resolve().parent.parent
        config_path = config_path or root / "config" / "brain.yaml"

        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        llm_cfg = self.config["llm"]
        paths = self.config["paths"]
        brain_cfg = self.config["brain"]

        self.root = root
        self.llm = OllamaProvider(
            model=llm_cfg["model"],
            base_url=llm_cfg["base_url"],
            max_tokens=llm_cfg.get("max_tokens", 4096),
        )

        data_dir = root / brain_cfg["data_dir"]
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "inbox").mkdir(exist_ok=True)
        (data_dir / "logs").mkdir(exist_ok=True)

        self.memory: MemoryBackend = create_memory(brain_cfg, llm_cfg, root)
        self.personality = BrainPersonality(
            workspace=paths["workspace"],
            user_notes_path=data_dir / "user_preferences.md",
        )
        self.tools = ToolRegistry(paths["workspace"])
        self.factory = AgentFactory(
            root / "config" / "agents.yaml",
            self.memory, self.personality, self.llm, self.tools,
            max_iterations=brain_cfg.get("max_agent_iterations", 30),
            brain_cfg=brain_cfg,
        )
        self.orchestrator = OrchestratorAgent(self.factory, self.memory, root)
        self.learning = BrainLearning(self.memory, self.llm.chat)
        self.bus = EventBus.get(root / brain_cfg.get("logs_dir", "data/logs") / "live_events.jsonl")
        self._ensure_session()
        self._wire_event_logging()

    def _wire_event_logging(self) -> None:
        if not hasattr(self.memory, "tool_calls"):
            return

        def _log_event(event: dict) -> None:
            etype = event.get("type", "")
            if etype in ("heartbeat", "task_start", "task_done", "task_error"):
                return
            try:
                self.memory.tool_calls.log_event(None, etype, event)
            except Exception:
                pass

        self.bus.subscribe(_log_event)

    def _ensure_session(self) -> None:
        if hasattr(self.memory, "ensure_conversation"):
            self.memory.ensure_conversation()

    def health_check(self) -> dict[str, Any]:
        from core.portfolio_bridge import PortfolioBridge

        inbox = self.root / "data" / "inbox"
        inbox_pending = len(list(inbox.glob("*.txt"))) if inbox.exists() else 0

        pg_ok = False
        if self.memory.storage_type == "postgres":
            try:
                with self.memory._conn() as conn:
                    conn.execute("SELECT 1")
                pg_ok = True
            except Exception:
                pg_ok = False

        bridge = PortfolioBridge.from_engine(self)
        model_pulled = self.llm.is_available()

        return {
            "ollama": model_pulled,
            "model": self.config["llm"]["model"],
            "agents": len(self.factory.list_all()),
            "storage": self.memory.storage_type,
            "postgres": pg_ok if self.memory.storage_type == "postgres" else "n/a",
            "memory_db": str(self.memory.db_path),
            "inbox_queue": inbox_pending,
            "portfolio_pending": bridge.pending_count(),
            "portfolio_done": bridge.completed_count(),
        }

    def get_session_resume_hint(self) -> str | None:
        if hasattr(self.memory, "conversations"):
            goal = self.memory.conversations.get_last_active_goal()
            if goal:
                return f"Last time we were working on: {goal[:80]}"
        return None

    def run(self, goal: str) -> dict[str, Any]:
        self._ensure_session()
        if hasattr(self.memory, "log_message"):
            self.memory.log_message("user", goal)

        result = self.orchestrator.execute(goal)
        task_id = result.get("task_id")
        steps = result.get("steps", [])

        if hasattr(self.memory, "log_tool_steps") and task_id:
            self.memory.log_tool_steps(task_id, steps)

        reply = result.get("result", "")
        if hasattr(self.memory, "log_message") and reply:
            self.memory.log_message("nexus", reply, task_id)

        if self.config["brain"].get("learn_from_every_task", True):
            self.learning.learn_from_task(
                goal, reply, steps, result.get("status") == "done",
            )

        if result.get("status") == "done":
            try:
                from core.portfolio_bridge import PortfolioBridge
                marked = PortfolioBridge.from_engine(self).on_task_success(goal)
                if marked:
                    self.bus.emit("portfolio_task_done", {"goal": goal[:200]})
            except Exception:
                pass

        return result

    def teach(self, preference: str) -> None:
        self.personality.append_user_preference(preference)
        self.remember(preference, category="preference")
        if hasattr(self.memory, "set_preference"):
            key = preference[:60].strip()
            self.memory.set_preference(key, preference)

    def create_agent(self, name: str, role: str, capabilities: list[str] | None = None) -> dict:
        agent = self.factory.create_agent(name, role, capabilities or ["run_command", "read_file", "write_file"])
        return {"id": agent.agent_id, "name": agent.name, "role": agent.role}

    def list_agents(self) -> list[dict]:
        return self.factory.list_all()

    def remember(self, content: str, category: str = "general") -> int:
        return self.memory.remember(content, category)

    def recall(self, query: str) -> list[str]:
        return self.memory.recall(query)
