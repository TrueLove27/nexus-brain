"""Orchestrator — the master brain that plans, delegates, and spawns agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.factory import AgentFactory
from core.capabilities import build_capabilities_reply, is_capabilities_question
from core.intent import is_conversational, is_simple_greeting, build_greeting_reply
from core.planner import try_direct

SELF_UI_PATTERNS = [
    r"upgrade.*ui",
    r"update.*ui",
    r"improve.*ui",
    r"own ui",
    r"nexus.*app",
    r"desktop app",
    r"nexus_app",
]


class OrchestratorAgent:
    def __init__(self, factory: AgentFactory, memory, project_root: Path | None = None):
        self.factory = factory
        self.memory = memory
        self.project_root = project_root or Path(__file__).resolve().parent.parent
        self.agent = factory.get("orchestrator")
        if not self.agent:
            raise RuntimeError("Orchestrator agent not loaded")

    @staticmethod
    def _is_self_ui_goal(goal: str) -> bool:
        g = goal.lower()
        return any(re.search(p, g) for p in SELF_UI_PATTERNS)

    def _self_ui_context(self) -> str:
        root = str(self.project_root)
        return (
            f"## Nexus self-UI task\n"
            f"Project root: {root}\n"
            f"UI file: {root}/ui/nexus_app.py\n"
            f"Use write_file or edit_file — do NOT open external editors.\n"
            f"After UI changes, tell the user to restart the app to see them.\n"
        )

    def _chat_reply(self, goal: str, history: str) -> str:
        memories = self.memory.recall(goal, limit=3)
        sys_prompt = self.factory.personality.system_prompt(
            memories, "Nexus — friendly assistant on the user's Windows machine"
        )
        chat_rules = (
            "\n\n## Right now\n"
            "The user is chatting — NOT asking you to do work on their computer.\n"
            "Reply naturally in 1-3 short sentences. Be warm and helpful.\n"
            "Do NOT use tools or start doing tasks.\n"
            "If they greet you, greet them back and mention 2-3 things you can help with "
            '(e.g. "I can manage files, write code, install apps").\n'
            "Never say 'check out the tools' or refer to hidden UI — always be specific."
        )
        user_content = goal
        if history:
            user_content = f"Past context (for reference only):\n{history[:800]}\n\nUser says: {goal}"

        return self.factory.llm.chat([
            {"role": "system", "content": sys_prompt + chat_rules},
            {"role": "user", "content": user_content},
        ], temperature=0.4)

    def execute(
        self,
        goal: str,
        *,
        resume_checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = self.memory.log_task(goal, "orchestrator")
        all_steps: list[dict] = []
        history = self.memory.get_history_context(goal)
        context_parts: list[str] = [history] if history else []

        if is_capabilities_question(goal):
            reply = build_capabilities_reply(self.project_root)
            self.memory.complete_task(task_id, reply, [])
            self.memory.remember(f"Capabilities: {goal}", category="chat")
            return {"status": "done", "result": reply, "agent": "Nexus", "steps": [], "task_id": task_id}

        if is_conversational(goal):
            if is_simple_greeting(goal):
                reply = build_greeting_reply()
            else:
                reply = self._chat_reply(goal, history)
            self.memory.complete_task(task_id, reply, [])
            self.memory.remember(f"Chatted: {goal} → {reply[:120]}", category="chat")
            return {"status": "done", "result": reply, "agent": "Nexus", "steps": [], "task_id": task_id}

        # Skip planner shortcuts when resuming — tool work already started.
        from core.react_checkpoint import checkpoint_has_progress, resume_context_block

        resuming = checkpoint_has_progress(resume_checkpoint)
        if not resuming:
            direct = try_direct(goal, self.factory.tools)
            if direct and direct.get("status") == "teach":
                pref = direct.get("preference", "")
                if hasattr(self.memory, "set_preference"):
                    self.memory.set_preference(pref[:60], pref)
                self.factory.personality.append_user_preference(pref)
                self.memory.remember(pref, category="preference")
                reply = f"Got it — I'll remember: {pref}"
                self.memory.complete_task(task_id, reply, [])
                if hasattr(self.memory, "log_message"):
                    self.memory.log_message("nexus", reply, task_id)
                return {"status": "done", "result": reply, "agent": "Nexus", "steps": [], "task_id": task_id}

            if direct and direct.get("status") == "done":
                self.memory.complete_task(task_id, direct["result"], direct.get("steps", []))
                self.memory.remember(f"Completed: {goal} → {direct['result'][:200]}", category="task")
                return {**direct, "agent": "direct", "task_id": task_id}

        resume_block = resume_context_block(resume_checkpoint) if resuming else ""
        if resume_block:
            context_parts.insert(0, resume_block)

        if self._is_self_ui_goal(goal):
            context_parts.insert(0, self._self_ui_context())
            agent_id = "coder"
        else:
            # Prefer the agent that last checkpointed when resuming.
            preferred = (resume_checkpoint or {}).get("agent_id") if resuming else None
            agent_id = preferred if preferred and self.factory.get(preferred) else self.factory.pick_agent_for_goal(goal)

        current_agent = self.factory.get(agent_id) or self.agent
        sub_goal = goal
        depth = 0
        max_depth = 5
        # Only the first ReAct invocation gets the durable resume payload.
        pending_checkpoint = resume_checkpoint if resuming else None

        while depth < max_depth:
            context = "\n".join(context_parts) if context_parts else ""
            run_kwargs: dict[str, Any] = {"context": context}
            if pending_checkpoint is not None:
                run_kwargs["resume_checkpoint"] = pending_checkpoint
                pending_checkpoint = None
            result = current_agent.run(sub_goal, **run_kwargs)
            all_steps.extend(result.get("steps", []))

            if result["status"] == "done":
                self.memory.complete_task(task_id, result["result"], all_steps)
                self.memory.remember(f"Completed: {goal} → {result['result'][:200]}", category="task")
                out = {"status": "done", "result": result["result"], "agent": current_agent.name,
                       "steps": all_steps, "task_id": task_id}
                if result.get("resumed") or resuming:
                    out["resumed"] = True
                return out

            if result["status"] == "delegate":
                delegate_id = result.get("delegate_to") or self.factory.pick_agent_for_goal(result.get("sub_goal", ""))
                specialist = self.factory.get(delegate_id)
                if specialist:
                    context_parts.append(f"Orchestrator delegated to {specialist.name}: {result.get('thought', '')}")
                    current_agent = specialist
                    sub_goal = result.get("sub_goal", goal)
                    depth += 1
                    continue

            if result["status"] == "create_agent":
                spec = result.get("spec", {})
                try:
                    new_agent = self.factory.create_agent(
                        spec.get("name", "Specialist"),
                        spec.get("role", "Handles a specific task type"),
                        spec.get("capabilities", ["run_command", "read_file", "write_file"]),
                    )
                    context_parts.append(f"Created new agent: {new_agent.name} ({new_agent.agent_id})")
                    current_agent = new_agent
                    sub_goal = spec.get("goal", goal)
                    depth += 1
                    continue
                except Exception as e:
                    all_steps.append({"action": "create_agent_failed", "error": str(e)})

            if result["status"] == "incomplete":
                self.memory.fail_task(task_id, result.get("result", "incomplete"))
                out = {"status": "incomplete", "result": result.get("result"), "steps": all_steps, "task_id": task_id}
                if result.get("resumed") or resuming:
                    out["resumed"] = True
                return out

            break

        self.memory.fail_task(task_id, "Orchestration depth exceeded")
        return {
            "status": "failed",
            "result": "Could not complete after delegation chain",
            "steps": all_steps,
            "task_id": task_id,
        }
