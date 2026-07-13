"""Core identity — the agent's trained behavior, not generic chatbot mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

CORE_IDENTITY = """You are Nexus — an autonomous system agent with your own brain.

You help your user in two modes:
1. **Chat** — greetings, questions about you, casual conversation → just reply naturally. No tools.
2. **Action** — when they want something done on their Windows machine → plan, use tools, execute.

## When to act (use tools)
Only when the user clearly wants work done: create files, run commands, install apps, fix code, automate tasks, etc.
Break hard tasks into steps, delegate to specialists when needed, verify results, learn from outcomes.

## When NOT to act (no tools)
- Greetings: hello, hi, hey
- Thanks, goodbye, small talk
- "What can you do?" / "Who are you?" — explain briefly, don't start doing things
- Never invent tasks the user didn't ask for

## Rules for action mode
- Be thorough: real execution, not just advice
- Be honest: if something failed, say so and try another approach
- Create specialist agents when no existing agent fits
- All actions happen on the user's Windows machine at: {workspace}

## Response format when using tools
Think step by step. When you need to act, output a JSON block:
```json
{{"thought": "why I'm doing this", "action": "tool_name", "args": {{...}}}}
```
When the task is fully complete, output:
```json
{{"thought": "summary", "action": "finish", "result": "what was accomplished"}}
```
If the user is only chatting, skip tools entirely and use finish with your reply:
```json
{{"thought": "just chatting", "action": "finish", "result": "your friendly reply"}}
```
"""


class BrainPersonality:
    def __init__(self, workspace: str, user_notes_path: Path):
        self.workspace = workspace
        self.user_notes_path = user_notes_path
        self.user_notes_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.user_notes_path.exists():
            self._write_default_notes()
        self.goal_prompt_builds = 0

    def _write_default_notes(self) -> None:
        self.user_notes_path.write_text(
            "# User Preferences (Nexus learns and updates this)\n\n"
            "- Work autonomously — don't ask unless truly blocked\n"
            "- Prefer practical solutions over theoretical explanations\n"
            "- Handle difficult tasks: coding, file ops, workflow automation\n"
            "- Windows system — use PowerShell for shell commands\n",
            encoding="utf-8",
        )

    def _fresh_preferences(self) -> str:
        return self.user_notes_path.read_text(encoding="utf-8")

    def system_prompt(self, memories: list[str], agent_role: str | None = None) -> str:
        identity = CORE_IDENTITY.format(workspace=self.workspace)
        user_notes = self._fresh_preferences()

        parts = [identity]
        if agent_role:
            parts.append(f"\n## Your current role\n{agent_role}\n")
        parts.append(f"\n## User preferences (trained over time)\n{user_notes}\n")
        if memories:
            parts.append("\n## Relevant memories from past tasks\n")
            for m in memories:
                parts.append(f"- {m}\n")
        return "".join(parts)

    def system_prompt_for_goal(
        self,
        goal: str,
        memory: Any,
        *,
        role: str | None = None,
        max_memories: int = 8,
        max_facts: int = 5,
        max_procedures: int = 3,
        max_learnings: int = 5,
        max_kg_lines: int = 12,
    ) -> str:
        """Rebuild identity + preferences + goal-conditioned cognitive context.

        Call this per task (not only at AgentFactory construct time) so specialists
        see fresh preferences, memories, facts, procedures, learnings, and KG snippets
        for the current goal.
        """
        identity = CORE_IDENTITY.format(workspace=self.workspace)
        user_notes = self._fresh_preferences()
        parts: list[str] = [identity]
        if role:
            parts.append(f"\n## Your current role\n{role}\n")
        parts.append(f"\n## User preferences (trained over time)\n{user_notes}\n")

        goal = (goal or "").strip()
        if goal:
            parts.append(f"\n## Current goal (context lens)\n{goal[:500]}\n")
            parts.extend(
                self._goal_conditioned_blocks(
                    goal,
                    memory,
                    max_memories=max_memories,
                    max_facts=max_facts,
                    max_procedures=max_procedures,
                    max_learnings=max_learnings,
                    max_kg_lines=max_kg_lines,
                )
            )

        self.goal_prompt_builds += 1
        return "".join(parts)

    def _goal_conditioned_blocks(
        self,
        goal: str,
        memory: Any,
        *,
        max_memories: int,
        max_facts: int,
        max_procedures: int,
        max_learnings: int,
        max_kg_lines: int,
    ) -> list[str]:
        blocks: list[str] = []

        # Memories (hybrid recall when Postgres + pgvector)
        try:
            recalled = memory.recall(goal, limit=max_memories) if hasattr(memory, "recall") else []
            if recalled:
                blocks.append("\n## Relevant memories (goal-conditioned)\n")
                for m in recalled:
                    blocks.append(f"- {str(m)[:220]}\n")
        except Exception:
            pass

        # Semantic facts
        try:
            if hasattr(memory, "search_facts") and max_facts > 0:
                from brain.memory_tiers import format_fact_line

                facts = memory.search_facts(goal, limit=max_facts) or []
                if facts:
                    blocks.append("\n## Semantic facts (durable knowledge)\n")
                    for f in facts:
                        blocks.append(f"{format_fact_line(f)}\n")
        except Exception:
            pass

        # Procedures
        try:
            if hasattr(memory, "search_procedures") and max_procedures > 0:
                from brain.memory_tiers import format_procedure_line

                procs = memory.search_procedures(goal, limit=max_procedures) or []
                if procs:
                    blocks.append("\n## Procedural patterns (how-to)\n")
                    for p in procs:
                        blocks.append(f"{format_procedure_line(p)}\n")
        except Exception:
            pass

        # Active learnings for this goal
        try:
            if hasattr(memory, "get_learnings_for_goal") and max_learnings > 0:
                learnings = memory.get_learnings_for_goal(goal, limit=max_learnings) or []
                if learnings:
                    blocks.append("\n## Active learnings for this goal\n")
                    for row in learnings:
                        lesson = str(row.get("lesson") or "")[:200]
                        if not lesson:
                            continue
                        conf = row.get("confidence")
                        conf_s = f" conf={float(conf):.2f}" if conf is not None else ""
                        outcome = row.get("outcome") or "?"
                        blocks.append(f"- [{outcome}{conf_s}] {lesson}\n")
        except Exception:
            pass

        # Knowledge-graph neighborhood snippets
        try:
            if max_kg_lines > 0:
                kg_block = self._kg_neighborhood_block(goal, memory, max_lines=max_kg_lines)
                if kg_block:
                    blocks.append(kg_block)
        except Exception:
            pass

        return blocks

    @staticmethod
    def _kg_neighborhood_block(goal: str, memory: Any, *, max_lines: int = 12) -> str:
        # Prefer memory helper (covers SQLite + Postgres backends)
        if hasattr(memory, "knowledge_graph_context"):
            raw = memory.knowledge_graph_context(goal) or ""
            if raw.strip():
                lines = raw.strip().splitlines()
                # Cap lines; keep header if present
                if len(lines) > max_lines + 1:
                    lines = lines[: max_lines + 1]
                return "\n" + "\n".join(lines) + "\n"

        from brain.knowledge_graph import KnowledgeGraph, format_neighborhood

        kg = KnowledgeGraph(memory)
        if hasattr(kg, "context_for_goal"):
            raw = kg.context_for_goal(goal, max_lines=max_lines) or ""
            if raw.strip():
                return "\n" + raw.strip() + "\n"
        nb = kg.neighborhood(query=goal, hops=1, limit=max(8, max_lines))
        if not nb.get("entity"):
            return ""
        lines = ["## Knowledge graph (neighborhood)"]
        lines.extend(format_neighborhood(nb, max_lines=max_lines))
        return "\n" + "\n".join(lines) + "\n" if len(lines) > 1 else ""

    def append_user_preference(self, note: str) -> None:
        current = self.user_notes_path.read_text(encoding="utf-8")
        self.user_notes_path.write_text(current + f"\n- {note}\n", encoding="utf-8")
