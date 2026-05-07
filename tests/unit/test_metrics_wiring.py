import pytest
from prometheus_client import REGISTRY


def test_all_metrics_registered():
    # prometheus_client registers Counters with base name (no _total suffix in .name)
    expected = [
        "chloe_actions",
        "chloe_actions_held_back",
        "chloe_budget_usd_today",
        "chloe_llm_errors",
        "chloe_chat_turns",
        "chloe_pending_confirmations",
        "chloe_voice_latency_seconds",
        "chloe_memory_writes",
        "chloe_initiative_ticks",
        "chloe_deliberation_calls",
        "chloe_db_migration_failures",
    ]
    import chloe.observability.metrics  # noqa: F401
    registered = {m.name for m in REGISTRY.collect()}
    for name in expected:
        assert name in registered, f"Metric {name!r} not registered"


def test_metrics_endpoint_returns_expected_names():
    import chloe.observability.metrics  # noqa: F401
    from prometheus_client import generate_latest
    output = generate_latest().decode()
    assert "chloe_actions" in output
    assert "chloe_budget_usd_today" in output
    assert "chloe_llm_errors" in output


def test_action_increments_counter():
    from chloe.observability.metrics import chloe_actions_total
    before = _get_counter_value(chloe_actions_total, {"tool": "notes", "verb": "append", "state": "executed"})
    chloe_actions_total.labels(tool="notes", verb="append", state="executed").inc()
    after = _get_counter_value(chloe_actions_total, {"tool": "notes", "verb": "append", "state": "executed"})
    assert after == before + 1


def test_memory_writes_counter_increments():
    from chloe.observability.metrics import chloe_memory_writes_total
    before = _get_counter_value(chloe_memory_writes_total, {"kind": "episodic"})
    chloe_memory_writes_total.labels(kind="episodic").inc()
    after = _get_counter_value(chloe_memory_writes_total, {"kind": "episodic"})
    assert after == before + 1


def test_initiative_ticks_counter_increments():
    from chloe.observability.metrics import chloe_initiative_ticks_total
    before = _get_counter_value(chloe_initiative_ticks_total, {"outcome": "idle"})
    chloe_initiative_ticks_total.labels(outcome="idle").inc()
    after = _get_counter_value(chloe_initiative_ticks_total, {"outcome": "idle"})
    assert after == before + 1


def _get_counter_value(counter, labels: dict) -> float:
    try:
        return counter.labels(**labels)._value.get()
    except Exception:
        return 0.0
