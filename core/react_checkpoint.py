"""Durable ReAct step checkpoints — compact, resume, and rebuild transcripts.

Rolling transcript compaction keeps multi-reclaim resumes inside LLM context:
older tool steps fold into a durable ``transcript_summary`` while only a
sliding window of recent steps is rebuilt as chat messages.
"""

from __future__ import annotations

import json
from typing import Any


CHECKPOINT_VERSION = 2

# Per-step storage caps (already-applied side effects; detail can be truncated).
_MAX_RESULT_CHARS = 2000
_MAX_ARG_CHARS = 500
_MAX_THOUGHT_CHARS = 500

# Rolling window: how many recent tool steps stay as full assistant/user pairs.
KEEP_RECENT_IN_TRANSCRIPT = 8
# Ceiling on steps retained in the durable checkpoint JSON (older → summary).
MAX_STORED_STEPS = 24
# Hard cap on the rolling summary text stored on the job row.
_MAX_SUMMARY_CHARS = 6000
# Live in-loop message pairs (assistant+user) kept after the goal preamble.
KEEP_LIVE_MESSAGE_PAIRS = 8


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
    if isinstance(steps, list) and len(steps) > 0:
        return True
    # Compacted-only progress still counts (all detail already folded into summary).
    if int(cp.get("compacted_count") or 0) > 0:
        return True
    summary = str(cp.get("transcript_summary") or "").strip()
    return bool(summary)


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


def _step_digest_line(step: dict[str, Any]) -> str:
    action = step.get("action") or "?"
    args = step.get("args") or {}
    detail = ""
    for key in ("path", "command", "query"):
        if key in args:
            detail = str(args[key])[:80]
            break
    result_snip = str(step.get("result") or "").replace("\n", " ")[:100]
    return f"- [{step.get('iteration')}] {action} {detail} → {result_snip}".rstrip()


def fold_steps_into_summary(summary: str, steps: list[dict[str, Any]]) -> str:
    """Append step digests into the rolling transcript summary; trim from the head."""
    if not steps:
        return (summary or "").strip()
    lines = [_step_digest_line(s) for s in steps]
    block = "\n".join(lines)
    prior = (summary or "").strip()
    if prior:
        merged = prior + "\n" + block
    else:
        merged = "## Compacted prior ReAct steps\n" + block
    if len(merged) <= _MAX_SUMMARY_CHARS:
        return merged
    # Drop oldest characters; keep a leading ellipsis on a line boundary.
    clipped = merged[-_MAX_SUMMARY_CHARS:]
    nl = clipped.find("\n")
    if nl >= 0 and nl < len(clipped) - 1:
        clipped = clipped[nl + 1 :]
    return "…\n" + clipped


def roll_stored_steps(
    steps: list[dict[str, Any]],
    *,
    transcript_summary: str = "",
    compacted_count: int = 0,
    max_stored: int = MAX_STORED_STEPS,
) -> tuple[list[dict[str, Any]], str, int]:
    """
    Fold overflow steps into transcript_summary so durable JSON stays bounded.

    Returns (retained_steps, summary, new_compacted_count).
    """
    summary = (transcript_summary or "").strip()
    compacted = max(0, int(compacted_count or 0))
    if len(steps) <= max_stored:
        return list(steps), summary, compacted
    overflow = steps[:-max_stored]
    retained = steps[-max_stored:]
    summary = fold_steps_into_summary(summary, overflow)
    compacted += len(overflow)
    return retained, summary, compacted


def build_checkpoint(
    *,
    steps: list[dict],
    next_iteration: int,
    recent_actions: list[str] | None = None,
    parse_failures: int = 0,
    wind_down_sent: bool = False,
    stuck_sent: bool = False,
    agent_id: str | None = None,
    transcript_summary: str = "",
    compacted_count: int = 0,
) -> dict[str, Any]:
    tool_steps = [
        compact_step(s)
        for s in steps
        if s.get("action") not in ("finish", "delegate", "create_agent")
    ]
    retained, summary, compacted = roll_stored_steps(
        tool_steps,
        transcript_summary=transcript_summary,
        compacted_count=compacted_count,
    )
    return {
        "version": CHECKPOINT_VERSION,
        "next_iteration": max(0, int(next_iteration)),
        "steps": retained,
        "transcript_summary": summary,
        "compacted_count": compacted,
        "recent_actions": list(recent_actions or [])[-8:],
        "parse_failures": int(parse_failures or 0),
        "wind_down_sent": bool(wind_down_sent),
        "stuck_sent": bool(stuck_sent),
        "agent_id": agent_id,
        "step_count": compacted + len(retained),
    }


def resume_context_block(checkpoint: dict[str, Any] | None) -> str:
    """Short context for orchestrator / agent when resuming mid-goal."""
    cp = normalize_checkpoint(checkpoint)
    if not checkpoint_has_progress(cp):
        return ""
    steps = cp.get("steps") or []
    total = int(cp.get("step_count") or (int(cp.get("compacted_count") or 0) + len(steps)))
    lines = [
        "## Resume from durable checkpoint",
        f"Interrupted after {total} successful tool step(s); "
        f"continue from iteration {cp.get('next_iteration', total)} — "
        "do not redo completed work unless verification is required.",
    ]
    summary = str(cp.get("transcript_summary") or "").strip()
    if summary:
        lines.append("Compacted earlier progress:")
        # Cap orchestrator context; full summary still used in message rebuild.
        for line in summary.splitlines()[-16:]:
            lines.append(line if line.startswith(("-", "…", "#")) else f"- {line}")
    if steps:
        lines.append("Recent completed steps:")
        for s in steps[-12:]:
            lines.append(_step_digest_line(s))
    return "\n".join(lines)


def _goal_user_content(goal: str, tool_docs: str, context: str) -> str:
    return (
        f"## Goal\n{goal}\n\n"
        f"## Available tools\n{tool_docs}\n\n"
        + (f"## Context\n{context}\n\n" if context else "")
        + "If this is just a greeting or casual chat, reply with action: finish and a short friendly message — do NOT use any tools.\n"
        + "Only use tools when the user clearly wants something done on their computer.\n"
        + "When the goal is done, call action: finish immediately. Do not keep exploring.\n"
        + "Reply with ONLY a single JSON object — no other text."
    )


def rebuild_messages_from_checkpoint(
    *,
    system_content: str,
    goal: str,
    tool_docs: str,
    context: str,
    checkpoint: dict[str, Any],
    keep_recent: int = KEEP_RECENT_IN_TRANSCRIPT,
) -> tuple[list[dict[str, str]], list[dict], int, list[str], int, bool, bool, str, int]:
    """
    Rebuild ReAct chat messages + control state from a stored checkpoint.

    Only the sliding window of recent steps become full tool transcript pairs;
    older work is injected as a single compacted user message.

    Returns:
        messages, steps, start_iteration, recent_actions, parse_failures,
        wind_down_sent, stuck_sent, transcript_summary, compacted_count
    """
    cp = normalize_checkpoint(checkpoint)
    stored = [compact_step(s) for s in (cp.get("steps") or []) if s.get("action")]
    start_i = int(cp.get("next_iteration") if cp.get("next_iteration") is not None else len(stored))
    recent_actions = [str(a) for a in (cp.get("recent_actions") or [])]
    parse_failures = int(cp.get("parse_failures") or 0)
    wind_down_sent = bool(cp.get("wind_down_sent"))
    stuck_sent = bool(cp.get("stuck_sent"))
    summary = str(cp.get("transcript_summary") or "").strip()
    compacted_count = int(cp.get("compacted_count") or 0)

    keep = max(1, int(keep_recent))
    if len(stored) > keep:
        older, window = stored[:-keep], stored[-keep:]
        # Fold stored-but-outside-window steps into the summary for THIS rebuild
        # without mutating durable compacted_count (those steps remain in `steps`).
        effective_summary = fold_steps_into_summary(summary, older)
    else:
        window = stored
        effective_summary = summary

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": _goal_user_content(goal, tool_docs, context)},
    ]

    if effective_summary:
        messages.append({
            "role": "user",
            "content": (
                "## Prior ReAct progress (compacted for context limits)\n"
                f"{effective_summary}\n\n"
                "Recent tool iterations follow. Continue toward the goal — "
                "do not repeat compacted work unless verification is required."
            ),
        })

    for step in window:
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

    total = int(cp.get("step_count") or (compacted_count + len(stored)))
    messages.append({
        "role": "user",
        "content": (
            f"## Resume note\n"
            f"Lease reclaim or runner restart interrupted this job after {total} "
            f"successful tool iteration(s)"
            + (f" ({compacted_count} compacted into summary)" if compacted_count else "")
            + ". Continue from where you left off — "
            "do not repeat completed tools unless you must verify their results."
        ),
    })

    return (
        messages,
        stored,
        max(0, start_i),
        recent_actions,
        parse_failures,
        wind_down_sent,
        stuck_sent,
        summary,
        compacted_count,
    )


def trim_react_messages(
    messages: list[dict[str, str]],
    *,
    keep_pairs: int = KEEP_LIVE_MESSAGE_PAIRS,
) -> list[dict[str, str]]:
    """
    Rolling-compact an in-flight ReAct message list so long single-lease runs
    stay within the same context budget as multi-reclaim resumes.

    Preserves system + initial goal user message; folds older tool pairs into
    one compacted user message placed immediately after the goal.
    """
    if len(messages) < 4:
        return messages
    keep = max(1, int(keep_pairs))
    head = messages[:2]  # system + goal
    tail = messages[2:]

    # Detect an existing compaction marker so we can re-roll it.
    existing_summary = ""
    if (
        tail
        and tail[0].get("role") == "user"
        and str(tail[0].get("content") or "").startswith("## Prior ReAct progress")
    ):
        existing_summary = str(tail[0].get("content") or "")
        # Strip heading for fold bookkeeping; keep body lines only when re-emitting.
        body = existing_summary.split("\n", 1)
        existing_summary = body[1].strip() if len(body) > 1 else ""
        # Drop the trailing instruction paragraph if present.
        for marker in (
            "\n\nRecent tool iterations follow.",
            "\nContinue toward the goal",
        ):
            idx = existing_summary.find(marker)
            if idx >= 0:
                existing_summary = existing_summary[:idx].strip()
                break
        tail = tail[1:]

    # Pair assistant+user tool turns; keep control prompts (wind-down etc.) attached to window.
    pair_width = 2
    max_tail = keep * pair_width + 4  # room for wind-down / stuck / resume notes
    if len(tail) <= max_tail:
        if existing_summary:
            return head + [{
                "role": "user",
                "content": (
                    "## Prior ReAct progress (compacted for context limits)\n"
                    f"{existing_summary}\n\n"
                    "Recent tool iterations follow. Continue toward the goal — "
                    "do not repeat compacted work unless verification is required."
                ),
            }] + tail
        return messages

    overflow = tail[:-max_tail]
    window = tail[-max_tail:]
    digest_lines: list[str] = []
    pending_action: dict[str, Any] | None = None
    for msg in overflow:
        role = msg.get("role")
        content = str(msg.get("content") or "")
        if role == "assistant":
            pending_action = None
            try:
                obj = json.loads(content)
                if isinstance(obj, dict) and obj.get("action"):
                    pending_action = {
                        "iteration": "?",
                        "action": obj.get("action"),
                        "args": obj.get("args") if isinstance(obj.get("args"), dict) else {},
                        "result": "",
                    }
                    continue
            except json.JSONDecodeError:
                pass
            digest_lines.append(f"- assistant: {content.replace(chr(10), ' ')[:120]}")
        elif role == "user" and pending_action is not None and content.startswith("Tool result for "):
            # Prefer the tool-result body over the "Tool result for X:" header alone.
            body = content.split("\n", 1)
            result_body = body[1].split("\n\nContinue")[0].strip() if len(body) > 1 else ""
            pending_action["result"] = result_body[:100]
            digest_lines.append(_step_digest_line(pending_action))
            pending_action = None
        elif role == "user" and content.startswith("Tool result for "):
            digest_lines.append(f"- {content.split(chr(10), 1)[0][:160]}")

    if pending_action is not None:
        digest_lines.append(_step_digest_line(pending_action))

    prior = existing_summary.strip()
    if digest_lines:
        block = "\n".join(digest_lines)
        summary = (prior + "\n" + block) if prior else ("## Compacted prior ReAct steps\n" + block)
    else:
        summary = prior
    if len(summary) > _MAX_SUMMARY_CHARS:
        clipped = summary[-_MAX_SUMMARY_CHARS:]
        nl = clipped.find("\n")
        if nl >= 0 and nl < len(clipped) - 1:
            clipped = clipped[nl + 1 :]
        summary = "…\n" + clipped

    if not summary.strip():
        return head + window

    return head + [{
        "role": "user",
        "content": (
            "## Prior ReAct progress (compacted for context limits)\n"
            f"{summary.strip()}\n\n"
            "Recent tool iterations follow. Continue toward the goal — "
            "do not repeat compacted work unless verification is required."
        ),
    }] + window
