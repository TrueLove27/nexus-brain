"""Idle consolidation: episodic episodes → semantic facts + procedural patterns."""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from brain.memory_tiers import memory_tiers_config

ChatFn = Callable[[list[dict[str, str]]], str]

CONSOLIDATION_PROMPT = """You are consolidating an AI agent's episodic memory into durable knowledge.

Given recent unconsolidated episodes below, extract:
1. SEMANTIC FACTS — durable truths, preferences, environment facts, lessons (not transient chatter)
2. PROCEDURAL PATTERNS — reusable how-to / when-to patterns (trigger + steps)

Return ONLY valid JSON (no markdown) in this exact shape:
{{
  "facts": [
    {{"content": "string", "confidence": 0.0-1.0, "category": "general|preference|tech|environment"}}
  ],
  "procedures": [
    {{"trigger_text": "when this situation", "content": "step1; step2; ...", "confidence": 0.0-1.0, "category": "general|workflow|debug"}}
  ]
}}

Rules:
- Prefer quality over quantity (0-6 facts, 0-4 procedures)
- Skip noise, greetings, one-off task status lines unless they imply durable knowledge
- confidence 0.5-0.9 typical; use 0.9+ only when clearly evidenced across episodes

Episodes:
{episodes}
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    # Strip markdown fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


class MemoryConsolidator:
    """Promote unconsolidated episodes into facts and procedures via LLM."""

    def __init__(
        self,
        memory: Any,
        llm_chat_fn: ChatFn,
        brain_cfg: dict | None = None,
    ):
        self.memory = memory
        self.llm_chat = llm_chat_fn
        self.cfg = memory_tiers_config(brain_cfg)

    def should_run(self, force: bool = False) -> bool:
        if not self.cfg.get("enabled", True):
            return False
        if force:
            return True
        if not hasattr(self.memory, "count_unconsolidated_episodes"):
            return False
        n = int(self.memory.count_unconsolidated_episodes())
        threshold = int(self.cfg.get("consolidate_every_n_episodes", 5) or 5)
        return n >= max(1, threshold)

    def run(self, force: bool = False, *, batch_size: int = 20) -> dict[str, Any]:
        """Consolidate recent episodes. Returns summary stats."""
        empty = {
            "ran": False,
            "reason": "skipped",
            "episodes_read": 0,
            "facts_written": 0,
            "procedures_written": 0,
            "episodes_marked": 0,
        }
        if not self.cfg.get("enabled", True):
            empty["reason"] = "disabled"
            return empty
        if not force and not self.should_run(force=False):
            unc = 0
            if hasattr(self.memory, "count_unconsolidated_episodes"):
                unc = int(self.memory.count_unconsolidated_episodes())
            empty["reason"] = f"below_threshold({unc})"
            empty["unconsolidated"] = unc
            return empty

        if not hasattr(self.memory, "get_unconsolidated_episodes"):
            empty["reason"] = "backend_unsupported"
            return empty

        episodes = self.memory.get_unconsolidated_episodes(limit=batch_size)
        if not episodes:
            empty["reason"] = "none_pending"
            return empty

        # Optional task/tool summaries from metadata already in episode content.
        episode_block = "\n".join(
            f"- [#{e.get('id')}|{e.get('source', '?')}] {(e.get('content') or '')[:500]}"
            for e in episodes
        )
        prompt = CONSOLIDATION_PROMPT.format(episodes=episode_block)

        extracted: dict[str, Any] = {"facts": [], "procedures": []}
        try:
            raw = self.llm_chat([{"role": "user", "content": prompt}])
            parsed = _extract_json(raw or "")
            if parsed:
                extracted = parsed
        except Exception as exc:
            return {
                "ran": False,
                "reason": f"llm_error:{exc}",
                "episodes_read": len(episodes),
                "facts_written": 0,
                "procedures_written": 0,
                "episodes_marked": 0,
            }

        episode_ids = [int(e["id"]) for e in episodes if e.get("id") is not None]
        facts_written = 0
        procedures_written = 0

        for fact in extracted.get("facts") or []:
            if not isinstance(fact, dict):
                continue
            content = str(fact.get("content") or "").strip()
            if len(content) < 8:
                continue
            try:
                conf = float(fact.get("confidence", 0.7))
            except (TypeError, ValueError):
                conf = 0.7
            category = str(fact.get("category") or "general")[:40]
            if hasattr(self.memory, "insert_fact"):
                self.memory.insert_fact(
                    content,
                    confidence=conf,
                    source_episode_ids=episode_ids,
                    category=category,
                )
                facts_written += 1

        for proc in extracted.get("procedures") or []:
            if not isinstance(proc, dict):
                continue
            trigger = str(proc.get("trigger_text") or "").strip()
            content = str(proc.get("content") or "").strip()
            if len(trigger) < 4 or len(content) < 8:
                continue
            try:
                conf = float(proc.get("confidence", 0.7))
            except (TypeError, ValueError):
                conf = 0.7
            category = str(proc.get("category") or "general")[:40]
            if hasattr(self.memory, "insert_procedure"):
                self.memory.insert_procedure(
                    trigger,
                    content,
                    confidence=conf,
                    category=category,
                )
                procedures_written += 1

        marked = 0
        if hasattr(self.memory, "mark_episodes_consolidated"):
            marked = int(self.memory.mark_episodes_consolidated(episode_ids) or 0)

        return {
            "ran": True,
            "reason": "ok",
            "episodes_read": len(episodes),
            "facts_written": facts_written,
            "procedures_written": procedures_written,
            "episodes_marked": marked,
            "unconsolidated_remaining": (
                int(self.memory.count_unconsolidated_episodes())
                if hasattr(self.memory, "count_unconsolidated_episodes")
                else None
            ),
        }
