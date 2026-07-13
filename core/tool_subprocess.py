"""Reclaim-safe tool subprocess tracking and process-tree reaping.

Tool shells spawned under a durable job lease are registered (in-memory + optional
durable jobs.active_children). When a lease is reclaimed or fencing is lost, the
orphan process tree is killed so a new owner cannot race double-work with the prior
runner's children.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from core.job_context import get_fence_token, get_job_id, get_runner_id

# Windows: new process group so we can signal/kill the tree independently.
_CREATE_NEW_PROCESS_GROUP = 0x00000200 if sys.platform == "win32" else 0

_lock = threading.RLock()
_tracked: dict[int, "TrackedChild"] = {}  # keyed by pid
_cancel_by_job: dict[int, threading.Event] = {}
# Optional durable persist hook: (job_id, fence_token, children_list) -> bool
_persist_hook: Any = None


@dataclass
class TrackedChild:
    pid: int
    job_id: int | None
    fence_token: int | None
    runner_id: str | None
    command: str
    created_at: str
    proc: subprocess.Popen | None = None
    pgid: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "job_id": self.job_id,
            "fence_token": self.fence_token,
            "runner_id": self.runner_id,
            "command": (self.command or "")[:400],
            "created_at": self.created_at,
            "pgid": self.pgid,
            **({k: v for k, v in self.meta.items() if k not in {"env"}}),
        }


def set_persist_hook(hook) -> None:
    """Bind a callable that persists the active child list for a fenced job."""
    global _persist_hook
    _persist_hook = hook


def arm_job_cancel(job_id: int) -> threading.Event:
    """Create/reset a cancel Event for this job (checked while tools run)."""
    ev = threading.Event()
    with _lock:
        _cancel_by_job[int(job_id)] = ev
    return ev


def signal_job_cancel(job_id: int) -> None:
    with _lock:
        ev = _cancel_by_job.get(int(job_id))
    if ev is not None:
        ev.set()


def clear_job_cancel(job_id: int) -> None:
    with _lock:
        _cancel_by_job.pop(int(job_id), None)


def is_job_cancelled(job_id: int | None = None) -> bool:
    jid = int(job_id) if job_id is not None else get_job_id()
    if jid is None:
        return False
    with _lock:
        ev = _cancel_by_job.get(int(jid))
    return bool(ev and ev.is_set())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _spawn_kwargs() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_process_tree(pid: int, *, grace_seconds: float = 0.4) -> dict[str, Any]:
    """Best-effort kill of pid and descendants. Safe if already dead."""
    pid = int(pid)
    result: dict[str, Any] = {"pid": pid, "killed": False, "method": None, "error": None}
    if pid <= 0:
        result["error"] = "invalid_pid"
        return result
    try:
        if sys.platform == "win32":
            # /T = tree; /F = force. Exit 128 when process already gone.
            completed = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            result["method"] = "taskkill"
            result["killed"] = completed.returncode in (0, 128)
            if completed.returncode not in (0, 128):
                result["error"] = (completed.stderr or completed.stdout or "")[:300]
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
                result["method"] = "killpg_term"
                time.sleep(max(0.0, grace_seconds))
                try:
                    os.killpg(pgid, signal.SIGKILL)
                    result["method"] = "killpg_kill"
                except ProcessLookupError:
                    pass
                result["killed"] = True
            except ProcessLookupError:
                result["killed"] = True
                result["method"] = "already_dead"
            except Exception:
                os.kill(pid, signal.SIGKILL)
                result["method"] = "kill"
                result["killed"] = True
    except Exception as exc:
        result["error"] = str(exc)[:300]
    return result


def _children_for_job_fence(job_id: int, fence_token: int | None) -> list[TrackedChild]:
    with _lock:
        out = []
        for child in _tracked.values():
            if child.job_id != int(job_id):
                continue
            if fence_token is not None and child.fence_token != int(fence_token):
                continue
            out.append(child)
        return out


def _persist_job_children(job_id: int, fence_token: int | None) -> None:
    hook = _persist_hook
    if hook is None or fence_token is None:
        return
    records = [c.to_record() for c in _children_for_job_fence(job_id, fence_token)]
    try:
        hook(int(job_id), int(fence_token), records)
    except Exception:
        pass


def register_child(
    proc: subprocess.Popen,
    *,
    command: str = "",
    job_id: int | None = None,
    fence_token: int | None = None,
    runner_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> TrackedChild:
    """Track an already-started Popen under the current (or explicit) job fence."""
    jid = int(job_id) if job_id is not None else get_job_id()
    fence = int(fence_token) if fence_token is not None else get_fence_token()
    runner = runner_id if runner_id is not None else get_runner_id()
    pid = int(proc.pid)
    pgid = None
    if sys.platform != "win32":
        try:
            pgid = os.getpgid(pid)
        except Exception:
            pgid = pid
    child = TrackedChild(
        pid=pid,
        job_id=int(jid) if jid is not None else None,
        fence_token=int(fence) if fence is not None else None,
        runner_id=runner,
        command=command or "",
        created_at=_now_iso(),
        proc=proc,
        pgid=pgid,
        meta=dict(meta or {}),
    )
    with _lock:
        _tracked[pid] = child
    if child.job_id is not None and child.fence_token is not None:
        _persist_job_children(child.job_id, child.fence_token)
    return child


def unregister_pid(pid: int) -> None:
    with _lock:
        child = _tracked.pop(int(pid), None)
    if child and child.job_id is not None and child.fence_token is not None:
        _persist_job_children(child.job_id, child.fence_token)


def reap_records(
    records: Sequence[Mapping[str, Any]] | None,
    *,
    reason: str = "reclaim",
) -> list[dict[str, Any]]:
    """Kill process trees described by durable/active child records."""
    results: list[dict[str, Any]] = []
    for rec in records or []:
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        # Prefer in-memory Popen terminate path then tree kill.
        with _lock:
            tracked = _tracked.get(pid)
        if tracked and tracked.proc is not None and tracked.proc.poll() is None:
            try:
                tracked.proc.terminate()
            except Exception:
                pass
        info = kill_process_tree(pid)
        info["reason"] = reason
        info["command"] = str(rec.get("command") or "")[:200]
        info["fence_token"] = rec.get("fence_token")
        results.append(info)
        unregister_pid(pid)
    return results


def reap_job_fence(
    job_id: int,
    fence_token: int | None = None,
    *,
    reason: str = "fenced_out",
    extra_records: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Kill in-memory children for a job(/fence) plus optional durable records."""
    signal_job_cancel(job_id)
    local = [c.to_record() for c in _children_for_job_fence(int(job_id), fence_token)]
    # Merge durable extras not already covered by pid.
    seen = {int(r["pid"]) for r in local if r.get("pid")}
    merged = list(local)
    for rec in extra_records or []:
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid and pid not in seen:
            merged.append(dict(rec))
            seen.add(pid)
    return reap_records(merged, reason=reason)


def popen_tracked(
    args: Sequence[str],
    *,
    command: str = "",
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    text: bool = True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    **extra,
) -> subprocess.Popen:
    """Popen under a fresh process group and register for reclaim reaping."""
    if is_job_cancelled():
        raise RuntimeError("lease reclaimed — refusing to spawn tool subprocess")

    kwargs: dict[str, Any] = {
        "args": list(args),
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
        "text": text,
        "stdout": stdout,
        "stderr": stderr,
        **_spawn_kwargs(),
        **extra,
    }
    # Drop None env so Popen inherits (callers usually pass scrubbed env).
    if kwargs.get("env") is None:
        kwargs.pop("env", None)

    proc = subprocess.Popen(**kwargs)
    register_child(proc, command=command or " ".join(str(a) for a in args))
    return proc


def run_tracked(
    args: Sequence[str],
    *,
    command: str = "",
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    text: bool = True,
    timeout: int | None = 120,
    **extra,
) -> subprocess.CompletedProcess:
    """subprocess.run equivalent with reclaim tracking + cancel-aware wait."""
    proc = popen_tracked(
        args,
        command=command,
        cwd=cwd,
        env=env,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **extra,
    )
    try:
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while proc.poll() is None:
            if is_job_cancelled():
                kill_process_tree(int(proc.pid))
                unregister_pid(int(proc.pid))
                raise RuntimeError("lease reclaimed — tool subprocess terminated")
            if deadline is not None and time.monotonic() >= deadline:
                kill_process_tree(int(proc.pid))
                unregister_pid(int(proc.pid))
                raise subprocess.TimeoutExpired(args, timeout)
            time.sleep(0.05)
        stdout, stderr = proc.communicate()
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=proc.returncode,
            stdout=stdout or "",
            stderr=stderr or "",
        )
    finally:
        if proc.pid:
            unregister_pid(int(proc.pid))


def active_children_snapshot(job_id: int | None = None) -> list[dict[str, Any]]:
    with _lock:
        children = list(_tracked.values())
    if job_id is not None:
        children = [c for c in children if c.job_id == int(job_id)]
    return [c.to_record() for c in children]


def status() -> dict[str, Any]:
    with _lock:
        n = len(_tracked)
        jobs = sorted({c.job_id for c in _tracked.values() if c.job_id is not None})
    return {
        "tracked_count": n,
        "jobs_with_children": jobs,
        "platform": sys.platform,
    }
