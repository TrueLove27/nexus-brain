"""Thread-safe current durable job context for execution traces and checkpoints."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Callable, Iterator

_current_job_id: ContextVar[int | None] = ContextVar("nexus_job_id", default=None)
_current_runner_id: ContextVar[str | None] = ContextVar("nexus_runner_id", default=None)
_current_fence_token: ContextVar[int | None] = ContextVar("nexus_fence_token", default=None)
_checkpoint_saver: ContextVar[Callable[[dict[str, Any]], bool] | None] = ContextVar(
    "nexus_checkpoint_saver", default=None
)


def get_job_id() -> int | None:
    return _current_job_id.get()


def set_job_id(job_id: int | None) -> Token:
    return _current_job_id.set(job_id)


def reset_job_id(token: Token) -> None:
    _current_job_id.reset(token)


def get_runner_id() -> str | None:
    return _current_runner_id.get()


def set_runner_id(runner_id: str | None) -> Token:
    return _current_runner_id.set(runner_id)


def reset_runner_id(token: Token) -> None:
    _current_runner_id.reset(token)


def get_fence_token() -> int | None:
    return _current_fence_token.get()


def set_fence_token(fence_token: int | None) -> Token:
    return _current_fence_token.set(fence_token)


def reset_fence_token(token: Token) -> None:
    _current_fence_token.reset(token)


def get_checkpoint_saver() -> Callable[[dict[str, Any]], bool] | None:
    return _checkpoint_saver.get()


def set_checkpoint_saver(saver: Callable[[dict[str, Any]], bool] | None) -> Token:
    return _checkpoint_saver.set(saver)


def reset_checkpoint_saver(token: Token) -> None:
    _checkpoint_saver.reset(token)


def save_step_checkpoint(checkpoint: dict[str, Any]) -> bool:
    """Persist a ReAct checkpoint via the saver bound for the current durable job."""
    saver = _checkpoint_saver.get()
    if saver is None:
        return False
    try:
        return bool(saver(checkpoint))
    except Exception:
        return False


@contextmanager
def job_scope(
    job_id: int | None,
    runner_id: str | None = None,
    *,
    fence_token: int | None = None,
    checkpoint_saver: Callable[[dict[str, Any]], bool] | None = None,
) -> Iterator[int | None]:
    """Bind tool_calls / agent_events / checkpoints to a durable job while in scope."""
    job_token = set_job_id(job_id)
    runner_token = set_runner_id(runner_id) if runner_id is not None else None
    fence_tok = set_fence_token(fence_token) if fence_token is not None else None
    saver_token = set_checkpoint_saver(checkpoint_saver) if checkpoint_saver is not None else None
    try:
        yield job_id
    finally:
        if saver_token is not None:
            reset_checkpoint_saver(saver_token)
        if fence_tok is not None:
            reset_fence_token(fence_tok)
        if runner_token is not None:
            reset_runner_id(runner_token)
        reset_job_id(job_token)


@contextmanager
def runner_scope(runner_id: str | None) -> Iterator[str | None]:
    """Bind tool/LLM rate limits to a durable-queue runner id."""
    token = set_runner_id(runner_id)
    try:
        yield runner_id
    finally:
        reset_runner_id(token)
