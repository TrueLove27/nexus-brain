"""Agent factory — Nexus creates new specialist agents on demand."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from agents.base import BaseAgent
from brain.memory import BrainMemory
from brain.personality import BrainPersonality
from tools.registry import ToolRegistry


class AgentFactory:
    def __init__(self, config_path: Path, memory: BrainMemory,
                 personality: BrainPersonality, llm, tools: ToolRegistry,
                 max_iterations: int = 25, brain_cfg: dict | None = None):
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        self.memory = memory
        self.personality = personality
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations
        self.brain_cfg = brain_cfg or {}
        self._agents: dict[str, BaseAgent] = {}
        self._spawn_dir = memory.data_dir / "spawned_agents"
        self._spawn_dir.mkdir(parents=True, exist_ok=True)
        self._load_builtin_agents()

    def _load_builtin_agents(self) -> None:
        for agent_id, spec in self.config.get("builtin_agents", {}).items():
            self._agents[agent_id] = self._build_agent(
                agent_id, spec["name"], spec["role"], spec.get("capabilities", []),
            )
            self.memory.register_agent(agent_id, spec["name"], spec["role"],
                                       spec.get("capabilities", []), "system")

    def _prompt_limits(self) -> dict[str, int]:
        tiers = self.brain_cfg.get("memory_tiers") or {}
        return {
            "max_memories": int(self.brain_cfg.get("max_context_memories", 8) or 8),
            "max_facts": int(tiers.get("max_facts_in_context", 5) or 5),
            "max_procedures": int(tiers.get("max_procedures_in_context", 3) or 3),
            "max_learnings": 5,
            "max_kg_lines": 12,
        }

    def _build_agent(self, agent_id: str, name: str, role: str,
                     capabilities: list[str]) -> BaseAgent:
        # Construct-time prompt is prefs + role only — goal-conditioned recall happens
        # on each BaseAgent.run / refresh_context(goal), not a stale role snapshot.
        cap_text = f"Your specialties: {', '.join(capabilities)}" if capabilities else ""
        role_desc = f"{name} — {role}\n{cap_text}".strip()
        sys_prompt = self.personality.system_prompt([], role_desc)
        return BaseAgent(
            agent_id, name, role, self.llm, self.tools, sys_prompt, self.max_iterations,
            max_parse_retries=self.brain_cfg.get("max_parse_retries", 5),
            stuck_action_threshold=self.brain_cfg.get("stuck_action_threshold", 3),
            wind_down_at_iteration=self.brain_cfg.get("wind_down_at_iteration"),
            memory=self.memory,
            personality=self.personality,
            role_description=role_desc,
            prompt_limits=self._prompt_limits(),
        )

    def context_refresh_stats(self) -> dict[str, Any]:
        """Aggregate refresh counts for health / diagnostics."""
        total = 0
        per_agent: dict[str, int] = {}
        for aid, agent in self._agents.items():
            n = int(getattr(agent, "context_refresh_count", 0) or 0)
            per_agent[aid] = n
            total += n
        builds = int(getattr(self.personality, "goal_prompt_builds", 0) or 0)
        return {
            "enabled": True,
            "agent_refreshes": total,
            "prompt_builds": builds,
            "per_agent": per_agent,
        }

    def get(self, agent_id: str) -> BaseAgent | None:
        return self._agents.get(agent_id)

    def list_all(self) -> list[dict[str, Any]]:
        return [{"id": aid, "name": a.name, "role": a.role} for aid, a in self._agents.items()]

    def create_agent(self, name: str, role: str, capabilities: list[str],
                     spawned_by: str = "orchestrator") -> BaseAgent:
        max_spawned = self.config.get("agent_creation", {}).get("max_spawned", 20)
        spawned_count = sum(1 for aid in self._agents if aid.startswith("custom_"))
        if spawned_count >= max_spawned:
            raise RuntimeError(f"Max spawned agents ({max_spawned}) reached")

        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        agent_id = f"custom_{slug}_{uuid.uuid4().hex[:6]}"

        spec = {"name": name, "role": role, "capabilities": capabilities, "spawned_by": spawned_by}
        spec_path = self._spawn_dir / f"{agent_id}.yaml"
        with open(spec_path, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)

        agent = self._build_agent(agent_id, name, role, capabilities)
        self._agents[agent_id] = agent
        self.memory.register_agent(agent_id, name, role, capabilities, spawned_by)
        return agent

    def pick_agent_for_goal(self, goal: str) -> str:
        """Heuristic routing — orchestrator uses this to delegate."""
        goal_lower = goal.lower()
        if any(w in goal_lower for w in ["ui", "interface", "nexus_app", "desktop app", "upgrade ui", "update ui"]):
            return "coder"
        if any(w in goal_lower for w in ["volume", "music", "song", "play", "spotify", "pause", "wallpaper", "mute"]):
            return "music_agent"
        if any(w in goal_lower for w in ["install", "download", "winget", "setup app", "github desktop"]):
            return "install_agent"
        if any(w in goal_lower for w in ["clone", "repository", "repo", "git clone", "pull request", "commit"]):
            return "repo_agent"
        if any(w in goal_lower for w in ["database", "query", "sql", "mongodb", "table", "select from"]):
            return "database_agent"
        if any(w in goal_lower for w in ["code", "script", "function", "class", "debug", "refactor", "api", "bug"]):
            return "coder"
        if any(w in goal_lower for w in ["file", "folder", "directory", "open", "move", "copy", "organize"]):
            return "file_agent"
        if any(w in goal_lower for w in ["run", "command", "process", "terminal", "shell"]):
            return "shell_agent"
        if any(w in goal_lower for w in ["workflow", "automate", "schedule", "pipeline", "integrate", "hook"]):
            return "workflow_agent"
        return "orchestrator"
