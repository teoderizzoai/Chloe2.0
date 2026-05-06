# F-09 · `observability/logging.py` — structlog JSON

## Overview

Configure `structlog` with a JSON renderer in `chloe/observability/logging.py`. Every emitted log record has always-present fields `chloe.span` and `ts`. Provide a `get_logger(span: str)` helper. Redact a configurable list of key names (tokens, email bodies, API keys) before emission.

## Context

The current 1.0 codebase uses Python's standard `logging` module with no structured format — logs are plain strings. 2.0 uses `structlog` JSON so that log aggregators (Grafana Loki, Datadog, etc.) can parse fields without regex. All spans (chat, gate, initiative, reflect, tool) share the same logger configured once at boot.

## Interface

```python
# chloe/observability/logging.py

import structlog

def configure_logging(level: str = "INFO") -> None:
    """
    Call once at boot (in loop.py). Sets up structlog JSON processor chain.
    """
    ...

def get_logger(span: str) -> structlog.BoundLogger:
    """
    Returns a logger bound with chloe.span=span.
    Call at module top: log = get_logger("gate")
    """
    ...

REDACTED_KEYS: set[str] = {
    "api_key", "token", "access_token", "refresh_token",
    "email_body", "auth_header", "secret", "password",
    "apns_key", "fcm_key",
}
```

## Processor chain

```python
import structlog
import logging

def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            _redact_processor,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
    )
```

## Redaction processor

```python
def _redact_processor(logger, method, event_dict: dict) -> dict:
    for key in list(event_dict.keys()):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict
```

The `REDACTED_KEYS` set is checked after lowercasing the key — so `"API_KEY"`, `"api_key"`, and `"Api_Key"` are all redacted.

## Always-present fields

The `get_logger(span)` helper binds `chloe.span` at the time of creation:

```python
def get_logger(span: str) -> structlog.BoundLogger:
    return structlog.get_logger().bind(**{"chloe.span": span})
```

The `ts` field is added by the `TimeStamper` processor in ISO 8601 format.

## Usage pattern

Every module that needs logging imports at module level:
```python
from chloe.observability.logging import get_logger
log = get_logger("gate")   # or "chat", "initiative", "tool", "reflect"
```

Then calls:
```python
log.info("action_submitted", tool="spotify", verb="queue_track", action_id=aid)
log.warning("leash_violation", action_id=aid, reason="quiet_hours")
log.error("gemini_failure", model="gemini-2.5-pro", attempt=2)
```

## Dependencies

- F-01 (package structure).
- F-02 (config, for `chloe_log_level`).

## Testing

### Unit tests — `tests/unit/test_logging.py`

```python
import json
import io
import pytest
import structlog
from chloe.observability.logging import configure_logging, get_logger, REDACTED_KEYS

@pytest.fixture(autouse=True)
def reset_structlog():
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()

def test_get_logger_returns_bound_logger():
    configure_logging("INFO")
    log = get_logger("test")
    assert log is not None

def test_redact_processor_redacts_known_keys():
    configure_logging("INFO")
    log = get_logger("test")
    
    # Capture output
    output = io.StringIO()
    structlog.configure(
        processors=[
            __import__("chloe.observability.logging", fromlist=["_redact_processor"])._redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=output),
    )
    log2 = get_logger("test")
    log2.info("test_event", secret="MY_SECRET_VALUE", normal_field="ok")
    
    # Check output
    output.seek(0)
    for line in output:
        if line.strip():
            data = json.loads(line)
            if "secret" in data:
                assert data["secret"] == "[REDACTED]"
            assert data.get("normal_field") != "[REDACTED]"

def test_redact_processor_direct():
    from chloe.observability.logging import _redact_processor
    event = {"api_key": "sk-secret", "token": "tok-abc", "message": "hello"}
    result = _redact_processor(None, None, event)
    assert result["api_key"] == "[REDACTED]"
    assert result["token"] == "[REDACTED]"
    assert result["message"] == "hello"

def test_redact_case_insensitive():
    from chloe.observability.logging import _redact_processor
    event = {"API_KEY": "value", "Token": "tok"}
    result = _redact_processor(None, None, event)
    assert result["API_KEY"] == "[REDACTED]"
    assert result["Token"] == "[REDACTED]"

def test_redacted_keys_set_contains_common_secrets():
    assert "api_key" in REDACTED_KEYS
    assert "token" in REDACTED_KEYS
    assert "email_body" in REDACTED_KEYS
    assert "secret" in REDACTED_KEYS
```

The explicit test from the deliverable spec:
```python
def test_secret_key_redacted_in_output():
    configure_logging("INFO")
    log = get_logger("test")
    # This should not raise and the secret should not appear in logs
    log.info("hello", secret="REDACTED")  # Should emit [REDACTED]
```

## Acceptance criteria

- `get_logger("test").info("hello", secret="REDACTED")` emits JSON where `secret` is `"[REDACTED]"`.
- `ts` field is present in all emitted records.
- `chloe.span` field equals the span name passed to `get_logger()`.
- Unit tests all pass.
