import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from chloe.initiative.engine import tick, realize, _score_candidate
from chloe.initiative.candidates import CandidateAction
from chloe.llm.schemas import OpportunityVector


def _make_candidate(tool="messages", verb="send_text", pressure=0.8, source="routine"):
    return CandidateAction(
        tool=tool, verb=verb, args={},
        intent="Test intent",
        pressure=pressure,
        source=source,
        source_id="test",
    )


def _neutral_opp():
    return OpportunityVector(
        messages=0.8, spotify=0.5, calendar=0.4,
        notes=0.7, web_search=0.7, gmail=0.3, reminders=0.4,
    )


def _patch_engine_deps(monkeypatch, pressure_candidates, *, mutex=False, gate=None, sleeping=False, energy=0.8):
    """Patch all engine dependencies for isolation."""
    monkeypatch.setattr("chloe.initiative.engine.is_sleep_window", lambda now: sleeping)
    monkeypatch.setattr("chloe.initiative.engine.pressure_driven_candidates", lambda s: pressure_candidates)
    monkeypatch.setattr("chloe.initiative.engine.goal_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.interest_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.routine_candidates", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine.curiosity_driven_candidates", lambda: [])
    monkeypatch.setattr("chloe.initiative.engine.share_queue_candidates", lambda: [])
    monkeypatch.setattr("chloe.initiative.engine.get_opportunity_vector", AsyncMock(return_value=_neutral_opp()))
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.0)
    monkeypatch.setattr("chloe.initiative.engine._load_inner_state_snapshot", lambda: {"goals": [], "interests": []})
    monkeypatch.setattr("chloe.initiative.engine._load_affect", lambda: {"energy": energy})
    monkeypatch.setattr("chloe.initiative.engine.audit_recent", AsyncMock(return_value=[]))
    monkeypatch.setattr("chloe.initiative.engine._get_threshold", lambda c=None: 0.35)
    monkeypatch.setattr("chloe.initiative.engine._tool_mutex_active", lambda t: mutex)
    if gate is not None:
        monkeypatch.setattr("chloe.initiative.engine.gate_submit", gate)


@pytest.mark.asyncio
async def test_tick_below_threshold_returns_none(monkeypatch):
    _patch_engine_deps(monkeypatch, [_make_candidate(pressure=0.1)])
    result = await tick()
    assert result is None


@pytest.mark.asyncio
async def test_tick_above_threshold_submits_to_gate(monkeypatch):
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))
    _patch_engine_deps(monkeypatch, [_make_candidate(pressure=0.9, source="routine")], gate=mock_gate)
    monkeypatch.setattr("chloe.initiative.engine.mark_routine_done", lambda s, n: None)

    result = await tick()
    assert result is not None
    mock_gate.assert_called_once()


@pytest.mark.asyncio
async def test_tick_mutex_blocks_action(monkeypatch):
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))
    _patch_engine_deps(monkeypatch, [_make_candidate(tool="messages", pressure=0.9)],
                       mutex=True, gate=mock_gate)

    result = await tick()
    assert result is None
    mock_gate.assert_not_called()


@pytest.mark.asyncio
async def test_tick_sleeping_returns_none(monkeypatch):
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))
    _patch_engine_deps(monkeypatch, [_make_candidate(pressure=0.9)],
                       gate=mock_gate, sleeping=True)

    result = await tick()
    assert result is None
    mock_gate.assert_not_called()


@pytest.mark.asyncio
async def test_tick_exhausted_returns_none(monkeypatch):
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))
    _patch_engine_deps(monkeypatch, [_make_candidate(pressure=0.9)],
                       gate=mock_gate, energy=0.10)

    result = await tick()
    assert result is None
    mock_gate.assert_not_called()


@pytest.mark.asyncio
async def test_tick_low_energy_suppresses_non_routine(monkeypatch):
    """Low energy reduces non-routine candidate scores below threshold."""
    mock_gate = AsyncMock(return_value=MagicMock(executed=True))
    # pressure-source candidate with energy=0.2 → score scaled down below threshold
    _patch_engine_deps(monkeypatch, [_make_candidate(pressure=0.5, source="pressure")],
                       gate=mock_gate, energy=0.2)

    result = await tick()
    assert result is None
    mock_gate.assert_not_called()
