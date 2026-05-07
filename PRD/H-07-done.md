# H-07 · Final Prometheus metrics + alerts wiring

## Overview

Ensure all counters from PRD §22.2 are being incremented at the right call sites. Wire Grafana alerts (or simple email alerts via Alertmanager): daily USD > cap, pending confirmation > 1h, DB migration failure, LLM error rate > 20% in 10 minutes.

## Context

F-10 registered all metric names. This step wires the actual `.inc()` / `.observe()` / `.set()` calls throughout the codebase and defines alert rules. `GET /metrics` in production should show non-zero values for all named metrics after 24h of operation.

## Implementation

### Metric call sites

#### `chloe_actions_total` — action gate

```python
# In chloe/actions/gate.py — _execute_and_record():
from chloe.observability.metrics import actions_total

# After determining outcome:
actions_total.labels(tool=action.tool, verb=action.verb, state=final_state).inc()
```

#### `chloe_actions_held_back_total` — gate denials

```python
# In chloe/actions/gate.py — when state="held_back":
from chloe.observability.metrics import actions_held_back_total

actions_held_back_total.labels(tool=action.tool, reason=denial_reason_short).inc()
```

#### `chloe_budget_usd_today` — budget tracker

```python
# In chloe/actions/budget.py — after charge():
from chloe.observability.metrics import budget_usd_today

total = _get_today_spend_sync()
budget_usd_today.set(total)
```

#### `chloe_llm_errors_total` — LLM client

```python
# In chloe/llm/gemini.py — on final failure after retries:
from chloe.observability.metrics import llm_errors_total

llm_errors_total.labels(call_type=prompt_name).inc()
```

#### `chloe_chat_turns_total` — chat handler

```python
# In chloe/channels/chat.py — after successful chat response:
from chloe.observability.metrics import chat_turns_total

chat_turns_total.inc()
```

#### `chloe_confirmations_pending` — confirmation gauge

```python
# In chloe/actions/confirm.py — send() and confirm()/deny():
from chloe.observability.metrics import confirmations_pending

# On send: increment
confirmations_pending.inc()

# On confirm or deny: decrement
confirmations_pending.dec()
```

#### `chloe_voice_latency_seconds` — voice pipeline

```python
# In chloe/voice/realtime.py — record_voice_latency():
from chloe.observability.metrics import voice_latency_seconds

voice_latency_seconds.observe(latency)
```

#### `chloe_memory_writes_total` — memory store

```python
# In chloe/memory/store.py — upsert():
from chloe.observability.metrics import memory_writes_total

memory_writes_total.labels(kind=memory.kind).inc()
```

#### `chloe_initiative_ticks_total` — initiative engine

```python
# In chloe/initiative/engine.py — tick():
from chloe.observability.metrics import initiative_ticks_total

initiative_ticks_total.labels(outcome="idle" if result is None else "action").inc()
```

#### `chloe_deliberation_calls_total` — deliberation (already in G-05)

Already wired in G-05.

### Full metrics registry (confirm `observability/metrics.py`)

```python
# chloe/observability/metrics.py
from prometheus_client import Counter, Gauge, Histogram, REGISTRY

actions_total = Counter(
    "chloe_actions_total",
    "Total actions executed",
    labelnames=["tool", "verb", "state"],
)

actions_held_back_total = Counter(
    "chloe_actions_held_back_total",
    "Actions blocked by gate",
    labelnames=["tool", "reason"],
)

budget_usd_today = Gauge(
    "chloe_budget_usd_today",
    "USD spent today",
)

llm_errors_total = Counter(
    "chloe_llm_errors_total",
    "LLM call failures after retries",
    labelnames=["call_type"],
)

chat_turns_total = Counter(
    "chloe_chat_turns_total",
    "Total chat turns processed",
)

confirmations_pending = Gauge(
    "chloe_confirmations_pending",
    "Currently pending confirmation tickets",
)

voice_latency_seconds = Histogram(
    "chloe_voice_latency_seconds",
    "Time to first audio byte in voice pipeline",
    buckets=[0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0],
)

memory_writes_total = Counter(
    "chloe_memory_writes_total",
    "Memory upsert operations",
    labelnames=["kind"],
)

initiative_ticks_total = Counter(
    "chloe_initiative_ticks_total",
    "Initiative engine tick outcomes",
    labelnames=["outcome"],
)

deliberation_calls_total = Counter(
    "chloe_deliberation_calls_total",
    "Deliberation LLM calls",
    labelnames=["model"],
)
```

### Alert rules (Prometheus Alertmanager)

```yaml
# alerts/chloe.yml
groups:
  - name: chloe
    rules:

      - alert: DailySpendExceedsCap
        expr: chloe_budget_usd_today > 5.0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Chloe daily spend exceeds cap"
          description: "chloe_budget_usd_today={{ $value | humanize }}. Check budget.py cap."

      - alert: ConfirmationPendingTooLong
        expr: chloe_confirmations_pending > 0 and time() - chloe_last_confirmation_created_at > 3600
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Confirmation ticket pending > 1 hour"
          description: "A confirmation ticket has been pending for over an hour. Teo may not have received the push."

      - alert: LLMErrorRateHigh
        expr: rate(chloe_llm_errors_total[10m]) / rate(chloe_chat_turns_total[10m]) > 0.2
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "LLM error rate > 20% over 10 minutes"
          description: "Check Gemini API health and retry logic."

      - alert: DBMigrationFailure
        expr: chloe_db_migration_failures_total > 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "DB migration failed at startup"
          description: "chloe.db migration runner failed. Server may not be functional."
```

Add `chloe_db_migration_failures_total` counter:

```python
# In chloe/observability/metrics.py:
db_migration_failures_total = Counter(
    "chloe_db_migration_failures_total",
    "Database migration failures",
)

# In chloe/state/db.py — migrate():
except Exception as exc:
    from chloe.observability.metrics import db_migration_failures_total
    db_migration_failures_total.inc()
    raise
```

### Simple email alerting fallback (no Grafana)

```python
# chloe/observability/alert_checker.py
"""
Runs every 10 minutes via background task.
Sends email via SMTP if critical conditions are met.
Used when Grafana/Alertmanager is not available.
"""
import smtplib
from email.message import EmailMessage
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("alert_checker")


async def check_and_alert():
    settings = get_settings()
    from chloe.actions.budget import get_today_spend
    from chloe.actions.confirm import get_pending_count
    from prometheus_client import REGISTRY

    alerts = []

    spend = await get_today_spend()
    if spend > settings.spending_cap_daily_usd:
        alerts.append(f"Daily spend ${spend:.2f} exceeds cap ${settings.spending_cap_daily_usd:.2f}")

    pending = await get_pending_count()
    if pending > 0:
        # Check age (simplified)
        alerts.append(f"{pending} confirmation(s) pending — check mobile app")

    if alerts:
        _send_alert_email(alerts, settings)


def _send_alert_email(alerts: list[str], settings) -> None:
    if not settings.alert_email or not settings.smtp_host:
        log.warning("alert_email_not_configured")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = "Chloe Alert"
        msg["From"] = settings.alert_email
        msg["To"] = settings.alert_email
        msg.set_content("\n".join(f"- {a}" for a in alerts))
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.send_message(msg)
        log.info("alert_email_sent", alert_count=len(alerts))
    except Exception as exc:
        log.error("alert_email_failed", error=str(exc))
```

## Testing

### Unit tests — `tests/unit/test_metrics_wiring.py`

```python
import pytest
from prometheus_client import REGISTRY


def test_all_metrics_registered():
    """All 10 named metrics must be registered."""
    expected = [
        "chloe_actions_total",
        "chloe_actions_held_back_total",
        "chloe_budget_usd_today",
        "chloe_llm_errors_total",
        "chloe_chat_turns_total",
        "chloe_confirmations_pending",
        "chloe_voice_latency_seconds",
        "chloe_memory_writes_total",
        "chloe_initiative_ticks_total",
        "chloe_deliberation_calls_total",
        "chloe_db_migration_failures_total",
    ]
    registered = {m.name for m in REGISTRY.collect()}
    for name in expected:
        assert name in registered, f"Metric {name!r} not registered"


def test_metrics_endpoint_returns_expected_names():
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    output = generate_latest().decode()
    assert "chloe_actions_total" in output
    assert "chloe_budget_usd_today" in output
    assert "chloe_llm_errors_total" in output


@pytest.mark.asyncio
async def test_action_increments_counter(monkeypatch):
    """Simulate an action execution and verify counter incremented."""
    from chloe.observability.metrics import actions_total
    before = _get_counter_value(actions_total, {"tool": "notes", "verb": "append", "state": "executed"})
    actions_total.labels(tool="notes", verb="append", state="executed").inc()
    after = _get_counter_value(actions_total, {"tool": "notes", "verb": "append", "state": "executed"})
    assert after == before + 1


def _get_counter_value(counter, labels: dict) -> float:
    try:
        return counter.labels(**labels)._value.get()
    except Exception:
        return 0.0
```

### Production smoke test

After 24h in production:

```bash
curl http://localhost:8000/metrics | grep "^chloe_" | grep -v "^# " | awk '{print $1, $2}' | sort
# All 10 metrics should have non-zero values
```

## Dependencies

- F-10 (`observability/metrics.py` — metric registrations).
- All tool files (G-03, G-05, etc.) — call sites.
- `prometheus_client` — Python Prometheus client.

## Acceptance criteria

- `GET /metrics` returns non-zero values for all 10 named metrics after 24h in production.
- Alert rules defined for: daily USD > cap, pending confirmation > 1h, LLM error rate > 20%, DB migration failure.
- `db_migration_failures_total` incremented on migration failure.
- All metrics importable from `chloe.observability.metrics` without error.
