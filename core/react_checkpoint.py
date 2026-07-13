"""Durable ReAct step checkpoints — compact, resume, and rebuild transcripts."""

from __future__ import annotations

import json
from typing import Any


CHECKPOINT_VERSION = 1
_MAX_RESULT_CHARS = 2000
_MAX_ARG_CHARS = 500
_MAX_THOUGHT_CHARS = 500


def normalize_checkpoint(raw: Any) -> dict[str, Any]:
    """Coerce DB/JSON checkpoint payloads into a dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def checkpoint_has_progress(checkpoint: dict[str, Any] | None) -> bool:
    cp = normalize_checkpoint(checkpoint)
    steps = cp.get("steps") or []
    return isinstance(steps, list) and len(steps) > 0


def compact_args(args: dict | None) -> dict:
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if isinstance(value, str):
            out[key] = value[:_MAX_ARG_CHARS]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        else:
            raw = json.dumps(value, default=str)
            out[key] = raw[:_MAX_ARG_CHARS]
    return out


def compact_step(step: dict[str, Any]) -> dict[str, Any]:
    """Shrink a ReAct step for durable storage (side effects already applied)."""
    return {
        "iteration": int(step.get("iteration") or 0),
        "action": str(step.get("action") or ""),
        "thought": str(step.get("thought") or "")[:_MAX_THOUGHT_CHARS],
        "args": compact_args(step.get("args") if isinstance(step.get("args"), dict) else {}),
        "result": str(step.get("result") or "")[:_MAX_RESULT_CHARS],
    }


def build_checkpoint(
    *,
    steps: list[dict],
    next_iteration: int,
    recent_actions: list[str] | None = None,
    parse_failures: int = 0,
    wind_down_sent: bool = False,
    stuck_sent: bool = False,
    agent_id: str | None = None,
) -> dict[str, Any]:
    tool_steps = [
        compact_step(s)
        for s in steps
        if s.get("action") not in ("finish", "delegate", "create_agent")
    ]
    return {
        "version": CHECKPOINT_VERSION,
        "next_iteration": max(0, int(next_iteration)),
        "steps": tool_steps,
        "recent_actions": list(recent_actions or [])[-8:],
        "parse_failures": int(parse_failures or 0),
        "wind_down_sent": bool(wind_down_sent),
        "stuck_sent": bool(stuck_sent),
        "agent_id": agent_id,
        "step_count": len(tool_steps),
    }


def resume_context_block(checkpoint: dict[str, Any] | None) -> str:
    """Short context for orchestrator / agent when resuming mid-goal."""
    cp = normalize_checkpoint(checkpoint)
    if not checkpoint_has_progress(cp):
        return ""
    steps = cp.get("steps") or []
    lines = [
        "## Resume from durable checkpoint",
        f"Interrupted after {len(steps)} successful tool step(s); "
        f"continue from iteration {cp.get('next_iteration', len(steps))} — "
        "do not redo completed work unless verification is required.",
        "Completed steps so far:",
    ]
    for s in steps[-12:]:
        action = s.get("action") or "?"
        args = s.get("args") or {}
        detail = ""
        for key in ("path", "command", "query"):
            if key in args:
                detail = str(args[key])[:80]
                break
        result_snip = str(s.get("result") or "").replace("\n", " ")[:100]
        lines.append(f"- [{s.get('iteration')}] {action} {detail} → {result_snip}")
    return "\n".join(lines)


def rebuild_messages_from_checkpoint(
    *,
    system_content: str,
    goal: str,
    tool_docs: str,
    context: str,
    checkpoint: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict], int, list[str], int, bool, bool]:
    """
    Rebuild ReAct chat messages + control state from a stored checkpoint.

    Returns:
        messages, steps, start_iteration, recent_actions, parse_failures,
        wind_down_sent, stuck_sent
    """
    cp = normalize_checkpoint(checkpoint)
    steps = [compact_step(s) for s in (cp.get("steps") or []) if s.get("action")]
    start_i = int(cp.get("next_iteration") if cp.get("next_iteration") is not None else len(steps))
    recent_actions = [str(a) for a in (cp.get("recent_actions") or [])]
    parse_failures = int(cp.get("parse_failures") or 0)
    wind_down_sent = bool(cp.get("wind_down_sent"))
    stuck_sent = bool(cp.get("stuck_sent"))

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_content},
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

    for step in steps:
        assistant_payload = {
            "thought": step.get("thought") or "",
            "action": step.get("action"),
            "args": step.get("args") or {},
        }
        messages.append({
            "role": "assistant",
            "content": json.dumps(assistant_payload, ensure_ascii=False),
        })
        messages.append({
            "role": "user",
            "content": (
                f"Tool result for {step.get('action')}:\n{step.get('result') or ''}\n\n"
                "Continue toward the goal. Call finish when done."
            ),
        })

    messages.append({
        "role": "user",
        "content": (
            f"## Resume note\n"
            f"Lease reclaim or runner restart interrupted this job after {len(steps)} "
            f"successful tool iteration(s). Continue from where you left off — "
            f"do not repeat completed tools unless you must verify their results."
        ),
    })

    return (
        messages,
        steps,
        max(0, start_i),
        recent_actions,
        parse_failures,
        wind_down_sent,
        stuck_sent,
    )
