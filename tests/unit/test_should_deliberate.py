import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from hypothesis import given, strategies as st
from chloe.actions.schema import Action
from chloe.actions.deliberate import (
    should_deliberate,
    _is_kinetic_sensitive,
    _budget_near_cap,
    _recent_outreach_spike,
    _high_cost_estimate,
    _repeated_same_verb,
)


def _make_action(**kwargs):
    defaults = dict(
        tool="spotify", verb="queue_track",
        args={}, intent="test", preview="test",
        authorization="kinetic",
    )
    defaults.update(kwargs)
    return Action(**defaults)


def test_kinetic_sensitive_always_deliberate():
    a = _make_action(authorization="kinetic-sensitive")
    assert _is_kinetic_sensitive(a) is True


def test_kinetic_does_not_trigger_sensitive_check():
    a = _make_action(authorization="kinetic")
    assert _is_kinetic_sensitive(a) is False


def test_budget_near_cap(monkeypatch):
    monkeypatch.setattr("chloe.actions.deliberate._throttle_level", lambda: 0.8)
    assert _budget_near_cap() is True


def test_budget_under_threshold(monkeypatch):
    monkeypatch.setattr("chloe.actions.deliberate._throttle_level", lambda: 0.5)
    assert _budget_near_cap() is False


def test_recent_outreach_spike(monkeypatch):
    now = datetime.utcnow()
    fake_actions = [
        _make_action(authorization="kinetic")
        for _ in range(3)
    ]
    for a in fake_actions:
        a.proposed_at = (now - timedelta(minutes=10)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _recent_outreach_spike() is True


def test_no_outreach_spike(monkeypatch):
    now = datetime.utcnow()
    fake_actions = [_make_action(authorization="kinetic")]
    fake_actions[0].proposed_at = (now - timedelta(minutes=10)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _recent_outreach_spike() is False


def test_high_cost_estimate():
    a = _make_action()
    a.cost_estimate = MagicMock(usd=0.15)
    assert _high_cost_estimate(a) is True


def test_low_cost_estimate():
    a = _make_action()
    a.cost_estimate = MagicMock(usd=0.05)
    assert _high_cost_estimate(a) is False


def test_no_cost_estimate():
    a = _make_action()
    a.cost_estimate = None
    assert _high_cost_estimate(a) is False


def test_repeated_same_verb(monkeypatch):
    now = datetime.utcnow()
    a = _make_action(tool="notes", verb="append")
    fake_actions = [_make_action(tool="notes", verb="append") for _ in range(3)]
    for fa in fake_actions:
        fa.proposed_at = (now - timedelta(hours=1)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _repeated_same_verb(a) is True


def test_not_repeated(monkeypatch):
    now = datetime.utcnow()
    a = _make_action(tool="notes", verb="append")
    fake_actions = [_make_action(tool="notes", verb="append")]
    fake_actions[0].proposed_at = (now - timedelta(hours=1)).isoformat()

    monkeypatch.setattr("chloe.actions.deliberate._audit_recent", lambda n: fake_actions)
    assert _repeated_same_verb(a) is False


@given(st.builds(
    Action,
    tool=st.just("gmail"),
    verb=st.just("send_reply"),
    args=st.just({"draftId": "d1"}),
    intent=st.text(min_size=1, max_size=50).filter(lambda s: bool(s.strip())),
    preview=st.text(min_size=1, max_size=50).filter(lambda s: bool(s.strip())),
    authorization=st.just("kinetic-sensitive"),
))
def test_kinetic_sensitive_always_true(action):
    """Property: any kinetic-sensitive action must always trigger deliberation."""
    with patch("chloe.actions.deliberate._throttle_level", return_value=0.0), \
         patch("chloe.actions.deliberate._audit_recent", return_value=[]):
        assert should_deliberate(action) is True
