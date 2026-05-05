# A-04 · `actions/leash.py` — leash checker

## Overview

Implement `chloe/actions/leash.py` as a pure function `violates(action, prefs, now) -> bool`. Checks quiet hours, `dont_touch` lists, `auth_ceiling`, and `away_mode`. No LLM calls. Reads preferences from the dict passed in (not the DB — the gate fetches prefs before calling this).

## Context

The leash is the hard safety layer that runs before any deliberation or execution. It must be a pure function (no I/O, no LLM) so it can be tested with property-based tests and called synchronously from the gate without async overhead. The gate fetches the preferences dict once and passes it to `violates()`.

## Preferences shape

The `prefs` dict has the following keys (from the `preferences` table defaults):

```python
{
    "quiet_hours": {
        "start": "23:00",      # HH:MM local time
        "end": "08:00",        # HH:MM local time
        "timezone": "Europe/Rome",
        "exempt_verbs": ["self.*"]   # glob patterns that bypass quiet hours
    },
    "dont_touch": {
        "gmail_labels": ["work", "legal"],
        "spotify_playlists": ["spotify:playlist:abc"],
        "notes_folders": ["Private"]
    },
    "auth_ceiling": "kinetic",     # max allowed authorization class
    "spending_cap_usd_day": 1.50,
    "focus_mode": False,
    "away_mode": False,
    "web_search_blocklist": []
}
```

## Implementation

```python
# chloe/actions/leash.py

from datetime import datetime
from fnmatch import fnmatch
from chloe.actions.schema import Action, AuthClass

AUTH_ORDER = ["free", "intimate", "kinetic", "kinetic-sensitive"]

def violates(action: Action, prefs: dict, now: datetime) -> tuple[bool, str]:
    """
    Returns (is_violated, reason_string).
    Checks in order: auth_ceiling → away_mode → quiet_hours → dont_touch.
    """
    # 1. Auth ceiling
    ceiling = prefs.get("auth_ceiling", "kinetic-sensitive")
    if AUTH_ORDER.index(action.authorization) > AUTH_ORDER.index(ceiling):
        return True, f"auth_ceiling={ceiling} blocks {action.authorization}"

    # 2. Away mode — suppresses all kinetic and above outreach
    if prefs.get("away_mode", False):
        if action.authorization in ("kinetic", "kinetic-sensitive"):
            if not _is_exempt(action, prefs.get("quiet_hours", {}).get("exempt_verbs", [])):
                return True, "away_mode is active"

    # 3. Quiet hours — suppresses kinetic and above outreach
    if _in_quiet_hours(now, prefs.get("quiet_hours", {})):
        if action.authorization in ("kinetic", "kinetic-sensitive"):
            if not _is_exempt(action, prefs.get("quiet_hours", {}).get("exempt_verbs", [])):
                return True, "quiet_hours active"

    # 4. Focus mode — suppresses messages only
    if prefs.get("focus_mode", False):
        if action.tool == "messages":
            return True, "focus_mode blocks outreach"

    # 5. Dont-touch lists
    violation = _check_dont_touch(action, prefs.get("dont_touch", {}))
    if violation:
        return True, violation

    return False, ""


def _in_quiet_hours(now: datetime, quiet_config: dict) -> bool:
    if not quiet_config:
        return False
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(quiet_config.get("timezone", "UTC"))
        local_now = now.astimezone(tz)
    except Exception:
        local_now = now

    start_h, start_m = map(int, quiet_config.get("start", "23:00").split(":"))
    end_h, end_m     = map(int, quiet_config.get("end",   "08:00").split(":"))

    current_minutes = local_now.hour * 60 + local_now.minute
    start_minutes   = start_h * 60 + start_m
    end_minutes     = end_h   * 60 + end_m

    if start_minutes > end_minutes:
        # Spans midnight (e.g., 23:00–08:00)
        return current_minutes >= start_minutes or current_minutes < end_minutes
    else:
        return start_minutes <= current_minutes < end_minutes


def _is_exempt(action: Action, exempt_verbs: list[str]) -> bool:
    verb_key = f"{action.tool}.{action.verb}"
    return any(fnmatch(verb_key, pattern) for pattern in exempt_verbs)


def _check_dont_touch(action: Action, dont_touch: dict) -> str | None:
    if action.tool == "gmail":
        label = action.args.get("label", "")
        for blocked in dont_touch.get("gmail_labels", []):
            if blocked.lower() in label.lower():
                return f"gmail label '{label}' is in dont_touch"

    if action.tool == "spotify":
        uri = action.args.get("uri", action.args.get("playlist_uri", ""))
        for blocked in dont_touch.get("spotify_playlists", []):
            if blocked == uri:
                return f"spotify playlist '{uri}' is in dont_touch"

    if action.tool == "notes":
        path = action.args.get("path", "")
        for blocked in dont_touch.get("notes_folders", []):
            if path.startswith(blocked):
                return f"notes folder '{blocked}' is in dont_touch"

    return None
```

## Dependencies

- A-02 (`Action` schema).

## Testing

### Unit tests — `tests/unit/test_leash.py`

```python
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

# Property-based test
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
```

## Acceptance criteria

- Pure function — no I/O, no async.
- Property test: for any `auth_ceiling="kinetic"`, any kinetic-sensitive action returns `True`.
- All unit tests pass.
- Quiet hours span-midnight logic correct (23:00–08:00 means 00:30 is blocked).
