"""Core identity — the agent's trained behavior, not generic chatbot mode."""

from __future__ import annotations

from pathlib import Path

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

    def _write_default_notes(self) -> None:
        self.user_notes_path.write_text(
            "# User Preferences (Nexus learns and updates this)\n\n"
            "- Work autonomously — don't ask unless truly blocked\n"
            "- Prefer practical solutions over theoretical explanations\n"
            "- Handle difficult tasks: coding, file ops, workflow automation\n"
            "- Windows system — use PowerShell for shell commands\n",
            encoding="utf-8",
        )

    def system_prompt(self, memories: list[str], agent_role: str | None = None) -> str:
        identity = CORE_IDENTITY.format(workspace=self.workspace)
        user_notes = self.user_notes_path.read_text(encoding="utf-8")

        parts = [identity]
        if agent_role:
            parts.append(f"\n## Your current role\n{agent_role}\n")
        parts.append(f"\n## User preferences (trained over time)\n{user_notes}\n")
        if memories:
            parts.append("\n## Relevant memories from past tasks\n")
            for m in memories:
                parts.append(f"- {m}\n")
        return "".join(parts)

    def append_user_preference(self, note: str) -> None:
        current = self.user_notes_path.read_text(encoding="utf-8")
        self.user_notes_path.write_text(current + f"\n- {note}\n", encoding="utf-8")
