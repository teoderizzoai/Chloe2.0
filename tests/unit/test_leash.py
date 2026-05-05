import pytest
from datetime import datetime, timezone
from hypothesis import given, strategies as st
from chloe.actions.schema import Action
from chloe.actions.leash import violates, AUTH_ORDER


def make_action(tool="messages", verb="send_text", auth="kinetic"):
    return Action(
        tool=tool, verb=verb, intent="test", preview="test",
        authorization=auth, args={},
    )


BASE_PREFS = {
    "quiet_hours": {"start": "23:00", "end": "08:00", "timezone": "UTC", "exempt_verbs": ["self.*"]},
    "dont_touch": {"gmail_labels": [], "spotify_playlists": [], "notes_folders": []},
    "auth_ceiling": "kinetic-sensitive",
    "focus_mode": False,
    "away_mode": False,
}

MIDDAY = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 5, 4, 0, 30, tzinfo=timezone.utc)


def test_free_action_passes_quiet_hours():
    a = make_action(auth="free")
    violated, _ = violates(a, BASE_PREFS, MIDNIGHT)
    assert not violated


def test_kinetic_blocked_during_quiet_hours():
    a = make_action(auth="kinetic")
    violated, reason = violates(a, BASE_PREFS, MIDNIGHT)
    assert violated
    assert "quiet_hours" in reason


def test_kinetic_allowed_outside_quiet_hours():
    a = make_action(auth="kinetic")
    violated, _ = violates(a, BASE_PREFS, MIDDAY)
    assert not violated


def test_auth_ceiling_blocks_kinetic_sensitive():
    prefs = {**BASE_PREFS, "auth_ceiling": "kinetic"}
    a = make_action(auth="kinetic-sensitive")
    violated, reason = violates(a, prefs, MIDDAY)
    assert violated
    assert "auth_ceiling" in reason


def test_auth_ceiling_allows_below_ceiling():
    prefs = {**BASE_PREFS, "auth_ceiling": "kinetic"}
    a = make_action(auth="intimate")
    violated, _ = violates(a, prefs, MIDDAY)
    assert not violated


def test_away_mode_blocks_kinetic():
    prefs = {**BASE_PREFS, "away_mode": True}
    a = make_action(auth="kinetic")
    violated, reason = violates(a, prefs, MIDDAY)
    assert violated
    assert "away_mode" in reason


def test_away_mode_allows_intimate():
    prefs = {**BASE_PREFS, "away_mode": True}
    a = make_action(auth="intimate")
    violated, _ = violates(a, prefs, MIDDAY)
    assert not violated


def test_dont_touch_gmail_label():
    prefs = {**BASE_PREFS, "dont_touch": {"gmail_labels": ["work"], "spotify_playlists": [], "notes_folders": []}}
    a = make_action(tool="gmail", verb="read_recent", auth="intimate")
    a = a.model_copy(update={"args": {"label": "work"}})
    violated, reason = violates(a, prefs, MIDDAY)
    assert violated
    assert "dont_touch" in reason or "gmail" in reason


def test_focus_mode_blocks_messages():
    prefs = {**BASE_PREFS, "focus_mode": True}
    a = make_action(tool="messages", auth="kinetic")
    violated, reason = violates(a, prefs, MIDDAY)
    assert violated
    assert "focus_mode" in reason


def test_exempt_verb_bypasses_quiet_hours():
    a = make_action(tool="self", verb="set_quiet", auth="free")
    violated, _ = violates(a, BASE_PREFS, MIDNIGHT)
    assert not violated


@given(
    auth=st.sampled_from(["free", "intimate", "kinetic", "kinetic-sensitive"]),
)
def test_kinetic_sensitive_always_violates_kinetic_ceiling(auth):
    prefs = {**BASE_PREFS, "auth_ceiling": "kinetic"}
    a = make_action(auth=auth)
    violated, _ = violates(a, prefs, MIDDAY)
    if AUTH_ORDER.index(auth) > AUTH_ORDER.index("kinetic"):
        assert violated
    else:
        assert not violated
