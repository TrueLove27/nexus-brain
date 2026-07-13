"""Self-training loop — extracts lessons from completed tasks."""

from __future__ import annotations

from .memory import BrainMemory


class BrainLearning:
    def __init__(self, memory: BrainMemory, llm_chat_fn):
        self.memory = memory
        self.llm_chat = llm_chat_fn

    def learn_from_task(self, goal: str, result: str, steps: list[dict], success: bool) -> None:
        outcome = "success" if success else "failure"
        step_summary = "\n".join(
            f"  - {s.get('action', '?')}: {str(s.get('result', ''))[:200]}"
            for s in steps[-10:]
        )
        # Episodic record of the task outcome (feeds consolidation)
        if hasattr(self.memory, "record_episode"):
            try:
                episode = (
                    f"Task {outcome}: {goal[:300]}\n"
                    f"Result: {result[:400]}\n"
                    f"Steps:\n{step_summary[:800]}"
                )
                self.memory.record_episode(
                    episode,
                    "task",
                    metadata={"goal": goal[:200], "outcome": outcome, "step_count": len(steps)},
                )
            except Exception:
                pass

        prompt = (
            f"A task was {'completed successfully' if success else 'attempted but had issues'}.\n\n"
            f"Goal: {goal}\n"
            f"Result: {result}\n"
            f"Steps taken:\n{step_summary}\n\n"
            "Extract ONE concise lesson (max 2 sentences) that will help with similar future tasks. "
            "Focus on: what worked, what didn't, user preferences implied, technical gotchas."
        )
        try:
            lesson = self.llm_chat([{"role": "user", "content": prompt}])
            if lesson and len(lesson) > 10:
                self.memory.record_learning(goal, outcome, lesson.strip())
        except Exception:
            if success:
                self.memory.record_learning(goal, outcome, f"Completed: {result[:300]}")
