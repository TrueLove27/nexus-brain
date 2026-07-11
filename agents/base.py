"""Base agent — ReAct loop with tool execution."""

from __future__ import annotations

import json
import re
from typing import Any

from tools.registry import ToolRegistry
from core.events import EventBus
from core.summary import summarize_incomplete


class BaseAgent:
    def __init__(
        self, agent_id: str, name: str, role: str, llm, tools: ToolRegistry,
        system_prompt: str, max_iterations: int = 25,
        max_parse_retries: int = 5,
        stuck_action_threshold: int = 3,
        wind_down_at_iteration: int | None = None,
    ):
        self.agent_id = agent_id
        self.name = name
        self.role = role
        self.llm = llm
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.max_parse_retries = max_parse_retries
        self.stuck_action_threshold = stuck_action_threshold
        self.wind_down_at = wind_down_at_iteration or max(int(max_iterations * 0.8), max_iterations - 5)

    TOOL_EXAMPLES = """
## Tool call examples (copy this format exactly)

List a directory:
{"thought": "need to see folder contents", "action": "list_dir", "args": {"path": "C:\\\\Users\\\\cheki"}}

Read a file:
{"thought": "read the source", "action": "read_file", "args": {"path": "main.py"}}

Write a file:
{"thought": "save the change", "action": "write_file", "args": {"path": "ui/nexus_app.py", "content": "..."}}

When done:
{"thought": "task complete", "action": "finish", "result": "Listed 5 folders: Desktop, Documents, ..."}
"""

    WIND_DOWN_MSG = (
        "You are almost out of steps. You MUST respond with action: finish — "
        "summarize what you accomplished, what failed, and what is still needed."
    )
    STUCK_MSG = (
        "You appear stuck repeating the same action. Call finish now with: "
        "what you tried, what worked, what blocked you, and what the user should do next."
    )

    def run(self, goal: str, context: str = "") -> dict[str, Any]:
        tool_docs = self.tools.descriptions()
        messages = [
            {"role": "system", "content": self.system_prompt + self.TOOL_EXAMPLES},
            {"role": "user", "content": (
                f"## Goal\n{goal}\n\n"
                f"## Available tools\n{tool_docs}\n\n"
                + (f"## Context\n{context}\n\n" if context else "")
                + "If this is just a greeting or casual chat, reply with action: finish and a short friendly message — do NOT use any tools.\n"
                + "Only use tools when the user clearly wants something done on their computer.\n"
                + "When the goal is done, call action: finish immediately. Do not keep exploring.\n"
                + "Reply with ONLY a single JSON object — no other text."
            )},
        ]

        steps: list[dict] = []
        final_result = ""
        parse_failures = 0
        recent_actions: list[str] = []
        wind_down_sent = False
        stuck_sent = False

        for i in range(self.max_iterations):
            if i >= self.wind_down_at and not wind_down_sent:
                messages.append({"role": "user", "content": self.WIND_DOWN_MSG})
                wind_down_sent = True

            response = self.llm.chat(messages, temperature=0.0)
            action = self._parse_action(response)

            if not action:
                action = self._infer_action_from_text(response, goal)
            if not action:
                parse_failures += 1
                retry = self._parse_retry_message(parse_failures, goal)
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": retry})
                if parse_failures >= self.max_parse_retries:
                    messages.append({"role": "user", "content": self.WIND_DOWN_MSG})
                    wind_down_sent = True
                continue

            thought = action.get("thought", "")
            act = action.get("action", "")

            if act == "finish":
                final_result = action.get("result", thought)
                steps.append({"iteration": i, "action": "finish", "thought": thought, "result": final_result})
                break

            if act == "delegate":
                steps.append({"iteration": i, "action": "delegate", "thought": thought,
                              "args": action.get("args", {})})
                return {"status": "delegate", "delegate_to": action.get("args", {}).get("agent"),
                        "sub_goal": action.get("args", {}).get("goal", goal),
                        "steps": steps, "thought": thought}

            if act == "create_agent":
                steps.append({"iteration": i, "action": "create_agent", "args": action.get("args", {})})
                return {"status": "create_agent", "spec": action.get("args", {}), "steps": steps}

            args = action.get("args", {})
            action_key = self._action_key(act, args)
            recent_actions.append(action_key)
            if len(recent_actions) > self.stuck_action_threshold:
                recent_actions.pop(0)
            if (
                len(recent_actions) >= self.stuck_action_threshold
                and len(set(recent_actions)) == 1
                and not stuck_sent
            ):
                messages.append({"role": "user", "content": self.STUCK_MSG})
                stuck_sent = True

            EventBus.get().emit("agent_action", {"agent": self.name, "action": act, "thought": thought})
            tool_result = self.tools.execute(act, args)
            steps.append({"iteration": i, "action": act, "args": args, "result": tool_result[:2000]})

            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"Tool result for {act}:\n{tool_result}\n\nContinue toward the goal. Call finish when done."})

        else:
            final_result = summarize_incomplete(steps, goal)
            return {"status": "incomplete", "result": final_result, "steps": steps}

        return {"status": "done", "result": final_result, "steps": steps}

    @staticmethod
    def _action_key(action: str, args: dict) -> str:
        key_parts = [action]
        for k in ("path", "command", "query"):
            if k in args:
                key_parts.append(str(args[k])[:80])
        return "|".join(key_parts)

    def _parse_retry_message(self, attempt: int, goal: str) -> str:
        base = 'INVALID. Reply with ONLY raw JSON, no markdown:\n{"thought":"reason","action":"tool_name","args":{}}'
        if attempt >= 2 and any(w in goal.lower() for w in ("code", "ui", "file", "write", "edit")):
            return base + '\nExample: {"thought":"done","action":"write_file","args":{"path":"ui/nexus_app.py","content":"..."}}'
        return base

    @staticmethod
    def _parse_action(text: str) -> dict | None:
        patterns = [
            r"```json\s*(\{.*?\})\s*```",
            r"```\s*(\{.*?\})\s*```",
        ]
        for pat in patterns:
            for match in re.finditer(pat, text, re.DOTALL):
                obj = BaseAgent._try_load_json(match.group(1))
                if obj:
                    return obj

        try:
            start = text.index("{")
            depth = 0
            for end in range(start, len(text)):
                if text[end] == "{":
                    depth += 1
                elif text[end] == "}":
                    depth -= 1
                    if depth == 0:
                        obj = BaseAgent._try_load_json(text[start:end + 1])
                        if obj:
                            return obj
                        break
        except ValueError:
            pass

        for match in re.finditer(r'\{[^{}]*"action"[^{}]*\}', text, re.DOTALL):
            obj = BaseAgent._try_load_json(match.group(0))
            if obj:
                return obj
        return None

    @staticmethod
    def _try_load_json(raw: str) -> dict | None:
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and "action" in obj:
                return obj
        except json.JSONDecodeError:
            pass
        return None

    def _infer_action_from_text(self, text: str, goal: str) -> dict | None:
        lower = text.lower()
        if "list_dir" in lower or "list directory" in lower:
            m = re.search(r"[Cc]:\\[^\s\"'`,]+", goal) or re.search(r"[Cc]:\\[^\s\"'`,]+", text)
            path = m.group(0) if m else "."
            return {"thought": "inferred list_dir", "action": "list_dir", "args": {"path": path}}
        if "finish" in lower and ("complete" in lower or "done" in lower):
            return {"thought": "inferred finish", "action": "finish", "result": text[:1000]}
        return None
