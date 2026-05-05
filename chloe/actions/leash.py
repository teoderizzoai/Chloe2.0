from datetime import datetime
from fnmatch import fnmatch

from chloe.actions.schema import Action

AUTH_ORDER = ["free", "intimate", "kinetic", "kinetic-sensitive"]


def violates(action: Action, prefs: dict, now: datetime) -> tuple[bool, str]:
    """Returns (is_violated, reason_string).
    Checks in order: auth_ceiling → away_mode → quiet_hours → focus_mode → dont_touch.
    """
    ceiling = prefs.get("auth_ceiling", "kinetic-sensitive")
    if AUTH_ORDER.index(action.authorization) > AUTH_ORDER.index(ceiling):
        return True, f"auth_ceiling={ceiling} blocks {action.authorization}"

    exempt_verbs = prefs.get("quiet_hours", {}).get("exempt_verbs", [])

    if prefs.get("away_mode", False):
        if action.authorization in ("kinetic", "kinetic-sensitive"):
            if not _is_exempt(action, exempt_verbs):
                return True, "away_mode is active"

    if _in_quiet_hours(now, prefs.get("quiet_hours", {})):
        if action.authorization in ("kinetic", "kinetic-sensitive"):
            if not _is_exempt(action, exempt_verbs):
                return True, "quiet_hours active"

    if prefs.get("focus_mode", False):
        if action.tool == "messages":
            return True, "focus_mode blocks outreach"

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
    end_h, end_m = map(int, quiet_config.get("end", "08:00").split(":"))

    current_minutes = local_now.hour * 60 + local_now.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    if start_minutes > end_minutes:
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
