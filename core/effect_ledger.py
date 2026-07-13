"""Idempotent tool-call effect ledger for reclaim-safe resumes.

Durable jobs hash each tool action+args into an effect_key. After a successful
execution the result is stored in Postgres. On lease reclaim (or LLM retry of
the same mutation), ToolRegistry skips re-application and returns the cached
result — closing the race where a side effect ran but the checkpoint did not.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Callable

from core.job_context import get_fence_token, get_job_id, get_react_iteration, get_runner_id

_MAX_RESULT_CHARS = 4000
_MAX_ARG_CHARS = 2000

_lock = threading.RLock()
# Optional hooks bound by DurableJobQueue / engine when Postgres is available.
_lookup_hook: Callable[[int, str], dict[str, Any] | None] | None = None
_record_hook: Callable[..., dict[str, Any] | None] | None = None
_seed_hook: Callable[[int, list[dict[str, Any]]], int] | None = None
_skip_notify: Callable[[dict[str, Any]], None] | None = None

# In-process fallback when no durable backend is bound (tests / sqlite mode).
_memory: dict[tuple[int, str], dict[str, Any]] = {}


def set_ledger_hooks(
    *,
    lookup: Callable[[int, str], dict[str, Any] | None] | None = None,
    record: Callable[..., dict[str, Any] | None] | None = None,
    seed: Callable[[int, list[dict[str, Any]]], int] | None = None,
    on_skip: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Bind durable ledger persistence (typically from PostgresMemory)."""
    global _lookup_hook, _record_hook, _seed_hook, _skip_notify
    with _lock:
        _lookup_hook = lookup
        _record_hook = record
        _seed_hook = seed
        _skip_notify = on_skip


def clear_ledger_hooks() -> None:
    set_ledger_hooks(lookup=None, record=None, seed=None, on_skip=None)
    with _lock:
        _memory.clear()


def canonical_args(args: dict | None) -> dict[str, Any]:
    """Stable, truncated arg shape for hashing / storage."""
    out: dict[str, Any] = {}
    for key in sorted((args or {}).keys(), key=str):
        value = (args or {})[key]
        if isinstance(value, str):
            out[str(key)] = value[:_MAX_ARG_CHARS]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[str(key)] = value
        else:
            raw = json.dumps(value, sort_keys=True, default=str)
            out[str(key)] = raw[:_MAX_ARG_CHARS]
    return out


def make_effect_key(action: str, args: dict | None) -> str:
    """Deterministic key for one side-effect identity within a job."""
    payload = json.dumps(
        {"action": str(action or ""), "args": canonical_args(args)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_successful_result(result: str) -> bool:
    text = (result or "").strip()
    if not text:
        return True
    lower = text.lower()
    # Do not lock in transient lease/cancel failures or generic tool errors.
    if lower.startswith("error:"):
        return False
    return True


def lookup_effect(job_id: int | None, effect_key: str) -> dict[str, Any] | None:
    if job_id is None or not effect_key:
        return None
    jid = int(job_id)
    hook = _lookup_hook
    if hook is not None:
        try:
            row = hook(jid, effect_key)
            if row and str(row.get("status") or "") == "applied":
                return row
            return None
        except Exception:
            return None
    with _lock:
        row = _memory.get((jid, effect_key))
    if row and str(row.get("status") or "") == "applied":
        return dict(row)
    return None


def record_effect(
    *,
    job_id: int | None,
    action: str,
    args: dict | None,
    result: str,
    effect_key: str | None = None,
    iteration: int | None = None,
    fence_token: int | None = None,
    runner_id: str | None = None,
) -> dict[str, Any] | None:
    """Persist an applied effect after a successful tool run. No-op on failure results."""
    if job_id is None:
        return None
    if not _is_successful_result(result):
        return None
    jid = int(job_id)
    key = effect_key or make_effect_key(action, args)
    iter_n = iteration if iteration is not None else get_react_iteration()
    fence = fence_token if fence_token is not None else get_fence_token()
    rid = runner_id if runner_id is not None else get_runner_id()
    safe_result = (result or "")[:_MAX_RESULT_CHARS]
    safe_args = canonical_args(args)

    hook = _record_hook
    if hook is not None:
        try:
            return hook(
                jid,
                key,
                action=str(action or ""),
                args=safe_args,
                result=safe_result,
                iteration=iter_n,
                fence_token=fence,
                runner_id=rid,
            )
        except Exception:
            return None

    row = {
        "job_id": jid,
        "effect_key": key,
        "action": str(action or ""),
        "args": safe_args,
        "result": safe_result,
        "iteration": iter_n,
        "fence_token": fence,
        "runner_id": rid,
        "status": "applied",
        "skipped_count": 0,
    }
    with _lock:
        existing = _memory.get((jid, key))
        if existing and existing.get("status") == "applied":
            return dict(existing)
        _memory[(jid, key)] = row
    return dict(row)


def seed_effects_from_steps(job_id: int | None, steps: list[dict[str, Any]] | None) -> int:
    """Upsert checkpoint / prior steps into the ledger so resumes skip their effects."""
    if job_id is None or not steps:
        return 0
    jid = int(job_id)
    prepared: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "")
        if not action or action in ("finish", "delegate", "create_agent"):
            continue
        result = str(step.get("result") or "")
        if not _is_successful_result(result):
            continue
        args = step.get("args") if isinstance(step.get("args"), dict) else {}
        prepared.append({
            "effect_key": make_effect_key(action, args),
            "action": action,
            "args": canonical_args(args),
            "result": result[:_MAX_RESULT_CHARS],
            "iteration": int(step.get("iteration") or 0),
        })
    if not prepared:
        return 0

    hook = _seed_hook
    if hook is not None:
        try:
            return int(hook(jid, prepared) or 0)
        except Exception:
            return 0

    seeded = 0
    with _lock:
        for item in prepared:
            key = item["effect_key"]
            slot = (jid, key)
            if slot not in _memory:
                _memory[slot] = {
                    "job_id": jid,
                    "effect_key": key,
                    "action": item["action"],
                    "args": item["args"],
                    "result": item["result"],
                    "iteration": item["iteration"],
                    "fence_token": get_fence_token(),
                    "runner_id": get_runner_id(),
                    "status": "applied",
                    "skipped_count": 0,
                }
                seeded += 1
    return seeded


def try_replay_effect(action: str, args: dict | None) -> str | None:
    """
    If the current durable job already applied this effect, return the cached
    result and notify listeners; otherwise return None (caller should execute).
    """
    job_id = get_job_id()
    if job_id is None:
        return None
    key = make_effect_key(action, args)
    row = lookup_effect(job_id, key)
    if not row:
        return None
    result = str(row.get("result") or "")
    meta = {
        "job_id": int(job_id),
        "effect_key": key,
        "action": str(action or ""),
        "iteration": row.get("iteration"),
        "fence_token": get_fence_token(),
        "runner_id": get_runner_id(),
        "skipped": True,
        "result_chars": len(result),
    }
    notify = _skip_notify
    if notify is not None:
        try:
            notify(meta)
        except Exception:
            pass
    return result


def execute_with_ledger(action: str, args: dict | None, run: Callable[[], str]) -> tuple[str, bool]:
    """
    Run a tool under the effect ledger.

    Returns (result, skipped) where skipped=True means the ledger replayed a
    prior application and ``run`` was not invoked.
    """
    replayed = try_replay_effect(action, args)
    if replayed is not None:
        return replayed, True
    result = run()
    record_effect(job_id=get_job_id(), action=action, args=args, result=result)
    return result, False


def status() -> dict[str, Any]:
    with _lock:
        mem_count = len(_memory)
    return {
        "durable_bound": _lookup_hook is not None and _record_hook is not None,
        "memory_entries": mem_count,
    }
