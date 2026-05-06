import pytest
from unittest.mock import AsyncMock, MagicMock
from chloe.initiative.engine import tick, _get_threshold


def test_threshold_unmodified_below_80_percent(monkeypatch):
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.5)
    monkeypatch.setattr("chloe.initiative.engine.get_settings",
                        lambda: MagicMock(initiative_threshold=0.35))
    assert abs(_get_threshold() - 0.35) < 0.001


def test_threshold_unchanged_at_exactly_80_percent(monkeypatch):
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.8)
    monkeypatch.setattr("chloe.initiative.engine.get_settings",
                        lambda: MagicMock(initiative_threshold=0.35))
    # multiplier = 1 + (0.8-0.8)*5 = 1.0 → no change
    assert abs(_get_threshold() - 0.35) < 0.001


def test_threshold_raised_at_90_percent(monkeypatch):
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 0.9)
    monkeypatch.setattr("chloe.initiative.engine.get_settings",
                        lambda: MagicMock(initiative_threshold=0.35))
    # multiplier = 1 + (0.9-0.8)*5 = 1.5 → 0.35 * 1.5 = 0.525
    expected = 0.35 * 1.5
    assert abs(_get_threshold() - expected) < 0.001


def test_threshold_exceeds_1_at_full_cap(monkeypatch):
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 1.0)
    monkeypatch.setattr("chloe.initiative.engine.get_settings",
                        lambda: MagicMock(initiative_threshold=0.6))
    # multiplier = 1 + (1.0-0.8)*5 = 2.0 → 0.6 * 2.0 = 1.2 > 1.0
    effective = _get_threshold()
    assert effective > 1.0, f"Expected threshold > 1.0 at full cap, got {effective}"


@pytest.mark.asyncio
async def test_tick_always_idle_when_throttle_100(monkeypatch):
    from chloe.initiative.candidates import CandidateAction
    from chloe.llm.schemas import OpportunityVector

    high = [CandidateAction(
        tool="messages", verb="send_text", args={},
        intent="test", pressure=1.0, source="test", source_id="t",
    )]

    opp = OpportunityVector(messages=1.0, spotify=1.0, calendar=1.0,
                             notes=1.0, web_search=1.0, gmail=1.0, reminders=1.0)

    monkeypatch.setattr("chloe.initiative.engine.pressure_driven_candidates", lambda s: high)
    monkeypatch.setattr("chloe.initiative.engine.goal_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.interest_driven_candidates", lambda g: [])
    monkeypatch.setattr("chloe.initiative.engine.routine_candidates", lambda n: [])
    monkeypatch.setattr("chloe.initiative.engine.get_opportunity_vector", AsyncMock(return_value=opp))
    monkeypatch.setattr("chloe.initiative.engine.throttle_level", lambda: 1.0)
    monkeypatch.setattr("chloe.initiative.engine._load_inner_state_snapshot",
                        lambda: {"goals": [], "interests": []})
    monkeypatch.setattr("chloe.initiative.engine._load_affect", lambda: {})
    monkeypatch.setattr("chloe.initiative.engine.audit_recent", AsyncMock(return_value=[]))
    monkeypatch.setattr("chloe.initiative.engine.get_settings",
                        lambda: MagicMock(initiative_threshold=0.6))

    result = await tick()
    assert result is None  # Full cap → effective threshold > 1.0 → always idle
