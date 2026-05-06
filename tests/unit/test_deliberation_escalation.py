import pytest
from unittest.mock import AsyncMock, patch

from chloe.actions.deliberate import (
    _high_cost_estimate,
    _is_kinetic_sensitive,
    should_deliberate,
    deliberate,
)
from chloe.actions.schema import Action, CostEstimate


def _make_action(auth_class="kinetic", cost_usd=0.0, tool="notes", verb="append"):
    return Action(
        tool=tool,
        verb=verb,
        args={},
        intent="test intent",
        preview="test preview",
        authorization=auth_class,
        cost_estimate=CostEstimate(usd=cost_usd),
    )


def test_high_cost_triggers_deliberation():
    action = _make_action(cost_usd=0.15)
    assert should_deliberate(action)


def test_low_cost_does_not_trigger_by_cost_alone():
    action = _make_action(cost_usd=0.05)
    assert not _high_cost_estimate(action)


def test_cost_threshold_boundary():
    assert _high_cost_estimate(_make_action(cost_usd=0.10)) is False
    assert _high_cost_estimate(_make_action(cost_usd=0.101)) is True


def test_zero_cost_is_low():
    assert _high_cost_estimate(_make_action(cost_usd=0.0)) is False


@pytest.mark.asyncio
async def test_kinetic_sensitive_high_cost_uses_pro_thinking():
    action = _make_action(auth_class="kinetic-sensitive", cost_usd=0.15)

    pro_called = []
    flash_called = []

    with patch("chloe.actions.deliberate.get_llm") as mock_get_llm:
        mock_llm = mock_get_llm.return_value
        mock_llm.pro_thinking = AsyncMock(
            return_value={"decision": "proceed", "reason": "ok"}
        )
        mock_llm.flash = AsyncMock(
            return_value={"decision": "proceed", "reason": "ok"}
        )
        mock_llm.pro_thinking.side_effect = lambda **kw: pro_called.append(kw) or {"decision": "proceed", "reason": "ok"}
        mock_llm.flash.side_effect = lambda *a, **kw: flash_called.append(kw) or {"decision": "proceed", "reason": "ok"}

        result = await deliberate(action, {})

    assert result is not None
    assert result.decision == "proceed"
    assert len(pro_called) == 1
    assert len(flash_called) == 0


@pytest.mark.asyncio
async def test_kinetic_high_cost_uses_flash():
    action = _make_action(auth_class="kinetic", cost_usd=0.15)

    pro_called = []
    flash_called = []

    with patch("chloe.actions.deliberate.get_llm") as mock_get_llm:
        mock_llm = mock_get_llm.return_value
        mock_llm.pro_thinking = AsyncMock()
        mock_llm.pro_thinking.side_effect = lambda **kw: pro_called.append(kw)
        mock_llm.flash = AsyncMock()
        mock_llm.flash.side_effect = lambda *a, **kw: flash_called.append(kw) or {"decision": "proceed", "reason": "ok"}

        result = await deliberate(action, {})

    assert len(flash_called) == 1
    assert len(pro_called) == 0


@pytest.mark.asyncio
async def test_kinetic_sensitive_low_cost_uses_flash():
    action = _make_action(auth_class="kinetic-sensitive", cost_usd=0.02)

    pro_called = []
    flash_called = []

    with patch("chloe.actions.deliberate.get_llm") as mock_get_llm:
        mock_llm = mock_get_llm.return_value
        mock_llm.pro_thinking = AsyncMock()
        mock_llm.pro_thinking.side_effect = lambda **kw: pro_called.append(kw)
        mock_llm.flash = AsyncMock()
        mock_llm.flash.side_effect = lambda *a, **kw: flash_called.append(kw) or {"decision": "proceed", "reason": "ok"}

        result = await deliberate(action, {})

    assert len(flash_called) == 1
    assert len(pro_called) == 0


@pytest.mark.asyncio
async def test_llm_failure_returns_none():
    action = _make_action(cost_usd=0.01)

    with patch("chloe.actions.deliberate.get_llm") as mock_get_llm:
        mock_llm = mock_get_llm.return_value
        mock_llm.flash = AsyncMock(side_effect=Exception("LLM timeout"))

        result = await deliberate(action, {})

    assert result is None


@pytest.mark.asyncio
async def test_deliberation_counter_incremented():
    from chloe.observability.metrics import deliberation_calls_total
    action = _make_action(cost_usd=0.01)

    before = deliberation_calls_total.labels(model="flash")._value.get()

    with patch("chloe.actions.deliberate.get_llm") as mock_get_llm:
        mock_llm = mock_get_llm.return_value
        mock_llm.flash = AsyncMock(return_value={"decision": "proceed", "reason": "ok"})
        await deliberate(action, {})

    after = deliberation_calls_total.labels(model="flash")._value.get()
    assert after > before
