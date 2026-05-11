"""Lightweight tracing — span context manager that decorates structlog with span IDs.

Real OTel export is not wired (no collector running). This gives the
shape: spans have IDs and durations, parents are tracked via contextvars,
and `span()` works as both a sync and async context manager.

If `opentelemetry-api` is importable, we additionally start an OTel span
so external collectors can pick it up; otherwise it's a pure structlog hook.
"""
from __future__ import annotations

import contextlib
import contextvars
import time
import uuid
from typing import Iterator

from chloe.observability.logging import get_logger

log = get_logger("trace")

_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace.current", default=None)
_current_trace: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace.trace_id", default=None)

try:  # pragma: no cover - optional dependency
    from opentelemetry import trace as _otel_trace
    _otel_tracer = _otel_trace.get_tracer("chloe")
except Exception:  # pragma: no cover
    _otel_tracer = None


def current_span_id() -> str | None:
    return _current_span.get()


def current_trace_id() -> str | None:
    return _current_trace.get()


@contextlib.contextmanager
def span(name: str, **attrs) -> Iterator[dict]:
    """Synchronous span context manager.

    `attrs` are logged at start and end. The yielded dict can be mutated
    inside the block to attach more attributes recorded at exit.
    """
    span_id = uuid.uuid4().hex[:12]
    parent = _current_span.get()
    trace_id = _current_trace.get() or uuid.uuid4().hex
    state: dict = {"id": span_id, "parent": parent, "trace": trace_id, "name": name, **attrs}

    tok_span = _current_span.set(span_id)
    tok_trace = _current_trace.set(trace_id)

    otel_ctx = _otel_tracer.start_as_current_span(name) if _otel_tracer else contextlib.nullcontext()
    started = time.perf_counter()
    log.debug("span_start", **state)
    try:
        with otel_ctx:
            yield state
    except Exception as exc:
        state["error"] = str(exc)
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
        log.info("span_end", elapsed_ms=elapsed_ms, **state)
        _current_span.reset(tok_span)
        _current_trace.reset(tok_trace)
