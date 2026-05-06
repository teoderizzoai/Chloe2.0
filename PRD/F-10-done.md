# F-10 · `observability/metrics.py` — Prometheus exporter

## Overview

Register all Prometheus counters and gauges listed in PRD §22.2 in `chloe/observability/metrics.py`. Expose a `/metrics` endpoint via FastAPI using `prometheus-client`. No data yet — just the registrations and the endpoint.

## Context

The current 1.0 codebase has no metrics. 2.0 introduces Prometheus instrumentation from day one so that Grafana dashboards and alerting (PRD §22.4) can be set up before the system goes fully live. All metric objects are registered at import time and incremented/set by the modules that own the data.

## Metrics to register

All from PRD §22.2:

```python
from prometheus_client import Counter, Gauge, CollectorRegistry

# Shared registry (use default_registry unless testing)

chloe_actions_total = Counter(
    "chloe_actions_total",
    "Total actions submitted to the gate",
    labelnames=["tool", "verb", "state"],
)

chloe_actions_held_back_total = Counter(
    "chloe_actions_held_back_total",
    "Total actions held back (suppressed, self_aborted, denied)",
    labelnames=["reason"],
)

chloe_llm_calls_total = Counter(
    "chloe_llm_calls_total",
    "Total LLM API calls made",
    labelnames=["model"],
)

chloe_llm_tokens_total = Counter(
    "chloe_llm_tokens_total",
    "Total tokens consumed",
    labelnames=["model", "kind"],  # kind: input | output | thinking
)

chloe_llm_usd_total = Counter(
    "chloe_llm_usd_total",
    "Total USD spent on LLM calls",
    labelnames=["model"],
)

chloe_affect_valence = Gauge(
    "chloe_affect_valence",
    "Current affect valence (-1 to 1)",
)

chloe_affect_arousal = Gauge(
    "chloe_affect_arousal",
    "Current affect arousal (0 to 1)",
)

chloe_pending_confirmations = Gauge(
    "chloe_pending_confirmations",
    "Number of confirmation tickets currently awaiting response",
)

chloe_chroma_size = Gauge(
    "chloe_chroma_size",
    "Number of documents in the Chroma memories collection",
)

chloe_kv_age_seconds = Gauge(
    "chloe_kv_age_seconds",
    "Seconds since the oldest kv entry was last updated",
)
```

## FastAPI integration

```python
# In chloe/observability/metrics.py

from fastapi import APIRouter
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

metrics_router = APIRouter()

@metrics_router.get("/metrics")
async def metrics_endpoint():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

In `app.py` (F-11):
```python
from chloe.observability.metrics import metrics_router
app.include_router(metrics_router)
```

## Helper functions

Provide convenience wrappers so callers don't import prometheus_client directly:

```python
def record_action(tool: str, verb: str, state: str) -> None:
    chloe_actions_total.labels(tool=tool, verb=verb, state=state).inc()

def record_held_back(reason: str) -> None:
    chloe_actions_held_back_total.labels(reason=reason).inc()

def record_llm_call(model: str, input_tokens: int, output_tokens: int,
                    thinking_tokens: int, usd: float) -> None:
    chloe_llm_calls_total.labels(model=model).inc()
    chloe_llm_tokens_total.labels(model=model, kind="input").inc(input_tokens)
    chloe_llm_tokens_total.labels(model=model, kind="output").inc(output_tokens)
    chloe_llm_tokens_total.labels(model=model, kind="thinking").inc(thinking_tokens)
    chloe_llm_usd_total.labels(model=model).inc(usd)

def set_affect(valence: float, arousal: float) -> None:
    chloe_affect_valence.set(valence)
    chloe_affect_arousal.set(arousal)

def set_pending_confirmations(n: int) -> None:
    chloe_pending_confirmations.set(n)

def set_chroma_size(n: int) -> None:
    chloe_chroma_size.set(n)
```

## Dependencies

- F-01 (package structure).
- F-11 (app.py router mounting — F-10 provides the router; F-11 mounts it).

## Testing

### Unit tests — `tests/unit/test_metrics.py`

```python
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

def make_test_app():
    from chloe.observability.metrics import metrics_router
    app = FastAPI()
    app.include_router(metrics_router)
    return app

def test_metrics_endpoint_returns_200():
    client = TestClient(make_test_app())
    response = client.get("/metrics")
    assert response.status_code == 200

def test_metrics_endpoint_contains_help_lines():
    client = TestClient(make_test_app())
    response = client.get("/metrics")
    body = response.text
    assert "# HELP chloe_actions_total" in body
    assert "# HELP chloe_llm_calls_total" in body
    assert "# HELP chloe_affect_valence" in body
    assert "# HELP chloe_pending_confirmations" in body

def test_record_action_increments_counter():
    from chloe.observability.metrics import record_action, chloe_actions_total
    before = chloe_actions_total.labels(tool="spotify", verb="queue_track", state="executed")._value.get()
    record_action("spotify", "queue_track", "executed")
    after = chloe_actions_total.labels(tool="spotify", verb="queue_track", state="executed")._value.get()
    assert after == before + 1

def test_set_affect_updates_gauge():
    from chloe.observability.metrics import set_affect, chloe_affect_valence
    set_affect(0.7, 0.5)
    assert chloe_affect_valence._value.get() == pytest.approx(0.7)

def test_record_llm_call_increments_all():
    from chloe.observability.metrics import record_llm_call, chloe_llm_calls_total
    before = chloe_llm_calls_total.labels(model="gemini-2.5-flash")._value.get()
    record_llm_call("gemini-2.5-flash", 100, 50, 0, 0.001)
    after = chloe_llm_calls_total.labels(model="gemini-2.5-flash")._value.get()
    assert after == before + 1
```

## Acceptance criteria

- `GET /metrics` returns HTTP 200.
- Response body contains `# HELP chloe_actions_total`.
- All 10 metrics from PRD §22.2 are registered (verified by checking `# HELP` lines).
- Helper functions increment/set the correct metric.
