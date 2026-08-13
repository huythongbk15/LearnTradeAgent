"""Correlation IDs for live-trading runs (P1.2).

A single ``run_id`` is minted per runner invocation and propagated through
the context-local variable so that every audit event, log line and error
raised inside the run can be correlated end-to-end.

Usage::

    from trading_agent.execution.correlation import (
        new_correlation_id,
        run_correlation,
    )

    with run_correlation(new_correlation_id()):
        ...  # append_live_audit_event() now tags every event with run_id
"""

from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar

_CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="")
_RUN_ID: ContextVar[str] = ContextVar("run_id", default="")


def new_correlation_id() -> str:
    """Return a fresh correlation identifier for one runner invocation."""
    return uuid.uuid4().hex


def set_correlation_id(value: str) -> None:
    """Bind the correlation ID for the current async/sync context."""
    _CORRELATION_ID.set(value)


def get_correlation_id() -> str:
    """Return the correlation ID bound to the current context ('' if unset)."""
    return _CORRELATION_ID.get()


def set_run_id(value: str) -> None:
    """Bind the run ID for the current context (alias for correlation)."""
    _RUN_ID.set(value)


def get_run_id() -> str:
    """Return the run ID bound to the current context ('' if unset)."""
    return _RUN_ID.get()


@contextlib.contextmanager
def run_correlation(correlation_id: str | None = None):
    """Context manager binding a correlation (and matching run) ID.

    Yields the bound ID.  The previous binding is restored on exit.
    """
    token = _CORRELATION_ID.set(correlation_id or new_correlation_id())
    run_token = _RUN_ID.set(_CORRELATION_ID.get())
    try:
        yield _CORRELATION_ID.get()
    finally:
        _CORRELATION_ID.reset(token)
        _RUN_ID.reset(run_token)


def bind_run_correlation() -> str:
    """Mint and bind a fresh correlation ID; returns it.

    Convenience for runner entry points that do not need the context manager
    form and want a single expression::

        run_id = bind_run_correlation()
    """
    cid = new_correlation_id()
    set_correlation_id(cid)
    set_run_id(cid)
    return cid
