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
from core.job_context import get_job_id, job_scope


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
        (data_dir / "inbox" / "processed").mkdir(exist_ok=True)
        (data_dir / "inbox" / "failed").mkdir(exist_ok=True)
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
                job_id = event.get("job_id")
                if job_id is None:
                    job_id = get_job_id()
                payload = dict(event)
                if job_id is not None:
                    payload.setdefault("job_id", job_id)
                self.memory.tool_calls.log_event(None, etype, payload, job_id=job_id)
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
        inbox_failed = len(list((inbox / "failed").glob("*.txt"))) if (inbox / "failed").exists() else 0
        inbox_retries = 0
        retries_path = inbox / "retries.json"
        if retries_path.exists():
            try:
                import json
                inbox_retries = len(json.loads(retries_path.read_text(encoding="utf-8")).get("tasks") or {})
            except Exception:
                inbox_retries = 0

        pg_ok = False
        if self.memory.storage_type == "postgres":
            try:
                with self.memory._conn() as conn:
                    conn.execute("SELECT 1")
                pg_ok = True
            except Exception:
                pg_ok = False

        job_pending = job_running = job_failed = 0
        job_traces: dict = {}
        if pg_ok and hasattr(self.memory, "job_queue_depth"):
            try:
                depth = self.memory.job_queue_depth()
                job_pending = depth.get("pending", 0)
                job_running = depth.get("running", 0)
                job_failed = depth.get("failed", 0)
            except Exception:
                pass
        if pg_ok and hasattr(self.memory, "job_trace_summary"):
            try:
                job_traces = self.memory.job_trace_summary(limit=3)
            except Exception:
                job_traces = {}

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
            "inbox_retries": inbox_retries,
            "inbox_failed": inbox_failed,
            "job_queue_pending": job_pending,
            "job_queue_running": job_running,
            "job_queue_failed": job_failed,
            "job_lease_reclaims": job_traces.get("lease_reclaim_total", 0),
            "job_failed_traces": job_traces.get("failed_trace_total", 0),
            "job_recent_reclaims": job_traces.get("recent_reclaims") or [],
            "job_recent_failures": job_traces.get("recent_failures") or [],
            "portfolio_pending": bridge.pending_count(),
            "portfolio_done": bridge.completed_count(),
        }

    def runtime_status(self, *, include_health: bool = True) -> dict[str, Any]:
        """Live session progress + inbox queue for desktop UI / status API."""
        from core.runtime_status import build_runtime_status

        health = self.health_check() if include_health else None
        return build_runtime_status(self.root, health=health)

    def get_session_resume_hint(self) -> str | None:
        if hasattr(self.memory, "conversations"):
            goal = self.memory.conversations.get_last_active_goal()
            if goal:
                return f"Last time we were working on: {goal[:80]}"
        return None

    def run(self, goal: str, *, job_id: int | None = None, job_attempt: int = 1) -> dict[str, Any]:
        """Execute a goal. When job_id is set, tool_calls/agent_events link to that durable job."""
        with job_scope(job_id):
            return self._run_inner(goal, job_id=job_id, job_attempt=job_attempt)

    def _run_inner(
        self,
        goal: str,
        *,
        job_id: int | None = None,
        job_attempt: int = 1,
    ) -> dict[str, Any]:
        self._ensure_session()
        if hasattr(self.memory, "log_message"):
            self.memory.log_message("user", goal)

        if job_id is not None:
            self.bus.emit("job_started", {
                "job_id": job_id,
                "attempt": job_attempt,
                "goal": goal[:200],
            })
            if hasattr(self.memory, "record_job_trace"):
                try:
                    self.memory.record_job_trace(
                        job_id,
                        "started",
                        attempt=job_attempt,
                        payload={"goal": goal[:200]},
                    )
                except Exception:
                    pass

        result = self.orchestrator.execute(goal)
        task_id = result.get("task_id")
        steps = result.get("steps", [])
        if job_id is not None:
            result = {**result, "job_id": job_id}

        if hasattr(self.memory, "log_tool_steps") and task_id:
            try:
                self.memory.log_tool_steps(task_id, steps, job_id=job_id)
            except TypeError:
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
                    self.bus.emit("portfolio_task_done", {"goal": goal[:200], "job_id": job_id})
            except Exception:
                pass

        return result

    def job_forensics(self, job_id: int) -> dict[str, Any] | None:
        """Aggregate job + traces + tool_calls + agent_events for debugging."""
        if hasattr(self.memory, "job_forensics"):
            return self.memory.job_forensics(job_id)
        return None

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
