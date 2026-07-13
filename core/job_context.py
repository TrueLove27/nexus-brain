"""Thread-safe current durable job context for execution traces."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

_current_job_id: ContextVar[int | None] = ContextVar("nexus_job_id", default=None)


def get_job_id() -> int | None:
    return _current_job_id.get()


def set_job_id(job_id: int | None) -> Token:
    return _current_job_id.set(job_id)


def reset_job_id(token: Token) -> None:
    _current_job_id.reset(token)


@contextmanager
def job_scope(job_id: int | None) -> Iterator[int | None]:
    """Bind tool_calls / agent_events writes to a durable job while in scope."""
    token = set_job_id(job_id)
    try:
        yield job_id
    finally:
        reset_job_id(token)
