"""Multi-tier memory constants and helpers.

Tiers:
  episodic   — raw episodes (task / chat / tool / teach)
  semantic   — durable facts distilled from episodes
  procedural — how-to / when-to patterns distilled from episodes
"""

from __future__ import annotations

from typing import Any

TIER_EPISODIC = "episodic"
TIER_SEMANTIC = "semantic"
TIER_PROCEDURAL = "procedural"

TIER_NAMES = (TIER_EPISODIC, TIER_SEMANTIC, TIER_PROCEDURAL)

EPISODE_SOURCES = ("task", "chat", "tool", "teach")

DEFAULT_MEMORY_TIERS_CONFIG: dict[str, Any] = {
    "enabled": True,
    "consolidate_every_n_episodes": 5,
    "max_facts_in_context": 5,
    "max_procedures_in_context": 3,
    "max_episodes_in_context": 4,
}


def normalize_source(source: str) -> str:
    s = (source or "task").strip().lower()
    return s if s in EPISODE_SOURCES else "task"


def memory_tiers_config(brain_cfg: dict | None) -> dict[str, Any]:
    """Merge brain.memory_tiers from config with defaults."""
    cfg = dict(DEFAULT_MEMORY_TIERS_CONFIG)
    raw = (brain_cfg or {}).get("memory_tiers") or {}
    if isinstance(raw, dict):
        cfg.update(raw)
    return cfg


def format_fact_line(fact: dict[str, Any]) -> str:
    conf = fact.get("confidence")
    prefix = f"[conf={conf:.2f}] " if isinstance(conf, (int, float)) else ""
    return f"- {prefix}{(fact.get('content') or '')[:220]}"


def format_procedure_line(proc: dict[str, Any]) -> str:
    trigger = (proc.get("trigger_text") or "")[:80]
    body = (proc.get("content") or "")[:180]
    return f"- WHEN '{trigger}': {body}"


def format_episode_line(ep: dict[str, Any]) -> str:
    src = ep.get("source") or "episode"
    return f"- [{src}] {(ep.get('content') or '')[:200]}"
