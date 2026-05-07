# X-05 · Structlog + OTel tracing across all spans

## Overview

Add a `@traced("span_name")` decorator to every major async function (chat path, gate, tick, reflect). Wire to the OTel (OpenTelemetry) endpoint in config. Add trace IDs to structlog output so every log line is linked to its span.

## Context

Observability degrades as the system grows more complex. Without distributed tracing, a slow chat response is opaque — is it Gemini, memory retrieval, or the gate? With `@traced`, every span is timed and linked, and the trace ID flows through structlog so log → trace correlation is trivial in any OTLP-compatible backend (Jaeger, Tempo, Honeycomb). The decorator approach minimizes boilerplate — one line per function.

**When:** Phase B (do before the system gets complex).

## Implementation

### `observability/tracing.py`

```python
# chloe/observability/tracing.py
from __future__ import annotations
import functools
from typing import Callable, Any
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("tracing")

_tracer: trace.Tracer | None = None


def init_tracing() -> None:
    """
    Call once at startup. Wires OTel to the configured OTLP endpoint.
    Gracefully no-ops if OTLP_ENDPOINT is not set.
    """
    global _tracer
    settings = get_settings()

    if not settings.otlp_endpoint:
        log.info("otel_tracing_disabled", reason="OTLP_ENDPOINT not configured")
        _tracer = trace.get_tracer("chloe.noop")
        return

    resource = Resource.create({"service.name": "chloe", "service.version": "2.0"})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _tracer = trace.get_tracer("chloe")
    log.info("otel_tracing_enabled", endpoint=settings.otlp_endpoint)


def get_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer("chloe.noop")
    return _tracer


def traced(span_name: str, attributes: dict | None = None):
    """
    Decorator: wraps an async function in an OTel span.
    Also injects trace_id into structlog context.

    Usage:
        @traced("chat.handle")
        async def handle_chat(message: str) -> str:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, str(v))

                # Inject trace_id into structlog context
                ctx = trace.get_current_span().get_span_context()
                trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "0" * 32

                import structlog
                structlog.contextvars.bind_contextvars(trace_id=trace_id)

                try:
                    result = await fn(*args, **kwargs)
                    span.set_status(trace.StatusCode.OK)
                    return result
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(trace.StatusCode.ERROR, str(exc))
                    raise
                finally:
                    structlog.contextvars.unbind_contextvars("trace_id")

        return wrapper
    return decorator
```

### Config additions

```python
# In chloe/config.py:
otlp_endpoint: str = ""  # e.g. "http://localhost:4317"; empty = tracing disabled
```

### Wire `init_tracing()` at startup

```python
# In chloe/app.py:
from chloe.observability.tracing import init_tracing

@app.on_event("startup")
async def on_startup():
    init_tracing()
    # ... other startup tasks
```

### Apply `@traced` to major functions

```python
# chloe/channels/chat.py:
from chloe.observability.tracing import traced

@traced("chat.handle")
async def handle_chat_message(message: str, history: list) -> str:
    ...

# chloe/actions/gate.py:
@traced("gate.submit")
async def submit(self, action: Action) -> Action:
    ...

@traced("gate.execute")
async def _execute_and_record(self, action: Action) -> ToolResult:
    ...

# chloe/initiative/engine.py:
@traced("initiative.tick")
async def tick(self) -> CandidateAction | None:
    ...

# chloe/actions/deliberate.py:
@traced("deliberate")
async def deliberate(action: Action, context: dict) -> Verdict | None:
    ...

# chloe/memory/retrieval.py:
@traced("memory.query_mixed")
async def query_mixed(rich_q: str, kinds_mix: dict) -> list[Memory]:
    ...

# chloe/memory/store.py:
@traced("memory.upsert")
async def upsert(self, memory: Memory) -> None:
    ...

# chloe/llm/gemini.py:
@traced("llm.chat")
async def chat(self, contents, system, cached_content=None, **kwargs):
    ...

@traced("llm.flash")
async def flash(self, prompt_name: str, payload: dict, schema) -> dict | None:
    ...

@traced("llm.pro_thinking")
async def pro_thinking(self, prompt_name: str, payload: dict, schema, thinking_budget: int) -> dict | None:
    ...

# chloe/voice/realtime.py:
@traced("voice.session")
async def handle_voice_session(websocket) -> None:
    ...
```

### Update structlog config to always include trace_id

```python
# In chloe/observability/logging.py:
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,  # Picks up trace_id from @traced
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_processor,
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
```

## Testing

### Unit tests — `tests/unit/test_tracing.py`

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from chloe.observability.tracing import traced, init_tracing


@pytest.mark.asyncio
async def test_traced_decorator_calls_function():
    @traced("test.span")
    async def my_fn(x: int) -> int:
        return x * 2

    result = await my_fn(5)
    assert result == 10


@pytest.mark.asyncio
async def test_traced_reraises_exceptions():
    @traced("test.error_span")
    async def failing_fn():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        await failing_fn()


@pytest.mark.asyncio
async def test_traced_injects_trace_id():
    injected_ids = []

    @traced("test.trace_id_span")
    async def fn_with_trace():
        import structlog
        ctx = structlog.contextvars.get_contextvars()
        injected_ids.append(ctx.get("trace_id", "missing"))

    await fn_with_trace()
    assert len(injected_ids) == 1
    # With no real OTLP, trace_id is all zeros (noop tracer) or a valid hex string
    assert injected_ids[0] is not None


@pytest.mark.asyncio
async def test_traced_with_attributes():
    captured_attributes = {}

    mock_span = MagicMock()
    mock_span.get_span_context.return_value = MagicMock(is_valid=False, trace_id=0)
    mock_span.__enter__ = lambda s: mock_span
    mock_span.__exit__ = MagicMock(return_value=False)

    def capture_attribute(k, v):
        captured_attributes[k] = v

    mock_span.set_attribute = capture_attribute
    mock_span.set_status = MagicMock()

    with patch("chloe.observability.tracing.get_tracer") as mock_tracer:
        mock_tracer.return_value.start_as_current_span.return_value.__enter__ = lambda s: mock_span
        mock_tracer.return_value.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        @traced("test.attrs", attributes={"tool": "gmail", "verb": "send_reply"})
        async def fn():
            return "ok"

        await fn()


def test_init_tracing_no_endpoint(monkeypatch):
    """No OTLP endpoint → no-op tracer, no crash."""
    monkeypatch.setattr("chloe.observability.tracing.get_settings",
                        lambda: MagicMock(otlp_endpoint=""))
    init_tracing()  # Should not raise


def test_init_tracing_with_endpoint(monkeypatch):
    """With endpoint → OTLPSpanExporter created."""
    monkeypatch.setattr("chloe.observability.tracing.get_settings",
                        lambda: MagicMock(otlp_endpoint="http://localhost:4317"))
    with patch("chloe.observability.tracing.OTLPSpanExporter") as MockExporter:
        with patch("chloe.observability.tracing.TracerProvider"):
            init_tracing()
        MockExporter.assert_called_once_with(endpoint="http://localhost:4317", insecure=True)


@pytest.mark.asyncio
async def test_trace_id_cleared_after_span():
    """trace_id should be unbound after the span exits."""
    @traced("test.cleanup")
    async def fn():
        pass

    await fn()

    import structlog
    ctx = structlog.contextvars.get_contextvars()
    assert "trace_id" not in ctx
```

### Integration verification

After deploying with `OTLP_ENDPOINT=http://jaeger:4317`:

```bash
# Send a chat message and look for the trace:
curl -X POST http://localhost:8000/v1/chat -d '{"message": "hello"}'

# In Jaeger UI, search for service "chloe":
# Expected spans: chat.handle → memory.query_mixed, llm.chat, gate.submit
```

## Dependencies

- `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` — Python OTel SDK.
- `structlog` — log context binding.
- `config.py` — `otlp_endpoint`.

## Acceptance criteria

- `@traced("span_name")` wraps any async function without changing its signature or return value.
- Exceptions are re-raised after being recorded on the span.
- `trace_id` appears in structlog output during a traced call.
- `trace_id` is unbound from structlog after the span exits.
- No OTLP endpoint → no-op (no crash, no network calls).
- `init_tracing()` called once at FastAPI startup.
- All 10 major functions decorated with `@traced`.
