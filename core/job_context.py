"""Thread-safe current durable job context for execution traces."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_current_job_id: ContextVar[int | None] = ContextVar("nexus_job_id", default=None)
_current_runner_id: ContextVar[str | None] = ContextVar("nexus_runner_id", default=None)


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


@contextmanager
def job_scope(job_id: int | None, runner_id: str | None = None) -> Iterator[int | None]:
    """Bind tool_calls / agent_events writes to a durable job while in scope."""
    job_token = set_job_id(job_id)
    runner_token = set_runner_id(runner_id) if runner_id is not None else None
    try:
        yield job_id
    finally:
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
