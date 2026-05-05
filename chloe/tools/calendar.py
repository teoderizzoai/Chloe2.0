from datetime import datetime, timedelta, timezone

import httpx

from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import load as load_token
from chloe.state.oauth_tokens import refresh as refresh_token
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.calendar")
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
CALENDAR_ID = "primary"


def _register_artifact(kind: str, ref: str, title: str, action_id: str) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO artifact_index (kind, ref, title, created_by_action, exists_) VALUES (?, ?, ?, ?, 1)",
        (kind, ref, title, action_id),
    )
    conn.commit()


class CalendarTool(Tool):
    name = "calendar"

    def __init__(self):
        self.verbs = {
            "read_today": ToolVerb(
                name="read_today",
                schema={"type": "object", "properties": {}},
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Read Teo's Google Calendar events for today.",
                description_for_human="Read today's calendar",
            ),
            "read_week": ToolVerb(
                name="read_week",
                schema={"type": "object", "properties": {}},
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Read Teo's Google Calendar events for this week.",
                description_for_human="Read week's calendar",
            ),
            "add_event": ToolVerb(
                name="add_event",
                schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start": {"type": "string", "description": "ISO 8601 datetime"},
                        "end": {"type": "string", "description": "ISO 8601 datetime"},
                        "description": {"type": "string"},
                    },
                    "required": ["title", "start", "end"],
                },
                auth_class="kinetic",
                reversibility=0.9,
                description_for_model="Add a calendar event to Teo's Google Calendar.",
                description_for_human="Add calendar event",
                reverse_verb="delete_event",
            ),
            "add_reminder": ToolVerb(
                name="add_reminder",
                schema={
                    "type": "object",
                    "properties": {"time": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["time", "body"],
                },
                auth_class="kinetic",
                reversibility=0.9,
                description_for_model="Add a reminder to Teo's calendar.",
                description_for_human="Add reminder",
            ),
            "decline": ToolVerb(
                name="decline",
                schema={
                    "type": "object",
                    "properties": {"eventId": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["eventId"],
                },
                auth_class="kinetic-sensitive",
                reversibility=0.3,
                description_for_model="Decline a calendar event on Teo's behalf. Requires confirmation.",
                description_for_human="Decline event",
            ),
            "delete_event": ToolVerb(
                name="delete_event",
                schema={"type": "object", "properties": {"eventId": {"type": "string"}}, "required": ["eventId"]},
                auth_class="kinetic",
                reversibility=0.0,
                description_for_model="Delete a Google Calendar event. Used as undo for add_event.",
                description_for_human="Delete calendar event",
            ),
            "find_free_slot": ToolVerb(
                name="find_free_slot",
                schema={
                    "type": "object",
                    "properties": {
                        "duration_minutes": {"type": "integer"},
                        "between": {
                            "type": "object",
                            "properties": {
                                "start": {"type": "string", "description": "ISO datetime"},
                                "end": {"type": "string", "description": "ISO datetime"},
                            },
                        },
                    },
                    "required": ["duration_minutes"],
                },
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Find a free time slot in Teo's calendar.",
                description_for_human="Find free slot",
            ),
        }

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "add_event":
            return f"Would add event '{args.get('title', '?')}' on {args.get('start', '?')}"
        if verb == "add_reminder":
            return f"Would set reminder at {args.get('time', '?')}: {args.get('body', '?')[:60]}"
        if verb == "decline":
            return f"Would decline event {args.get('eventId', '?')}"
        if verb == "delete_event":
            return f"Would delete event {args.get('eventId', '?')}"
        return super().dry_run(verb, args)

    async def _headers(self) -> dict | None:
        token = load_token("google")
        if not token:
            return None
        return {"Authorization": f"Bearer {token['access_token']}"}

    async def _get_events(self, time_min: str, time_max: str) -> list | None:
        hdrs = await self._headers()
        if not hdrs:
            return None
        params = {
            "calendarId": CALENDAR_ID,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                headers=hdrs,
                params=params,
            )
            if resp.status_code == 401:
                new_token = await refresh_token("google")
                if new_token:
                    hdrs = {"Authorization": f"Bearer {new_token['access_token']}"}
                    resp = await client.get(
                        f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                        headers=hdrs,
                        params=params,
                    )
            if resp.status_code != 200:
                log.error("calendar_api_error", status=resp.status_code)
                return None
            return resp.json().get("items", [])

    def _format_event(self, item: dict) -> dict:
        start = item.get("start", {})
        end = item.get("end", {})
        return {
            "title": item.get("summary", ""),
            "start": start.get("dateTime", start.get("date", "")),
            "end": end.get("dateTime", end.get("date", "")),
            "location": item.get("location"),
            "description": item.get("description", "")[:200],
            "id": item.get("id", ""),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        from chloe.config import get_settings
        tz_str = getattr(get_settings(), "chloe_timezone", "UTC")
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = timezone.utc

        now = datetime.now(tz)

        if verb == "read_today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            events = await self._get_events(start.isoformat(), end.isoformat())
            if events is None:
                return ToolResult(success=False, error="Failed to read calendar")
            return ToolResult(success=True, data={"events": [self._format_event(e) for e in events]})

        if verb == "read_week":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            events = await self._get_events(start.isoformat(), end.isoformat())
            if events is None:
                return ToolResult(success=False, error="Failed to read calendar")
            return ToolResult(success=True, data={"events": [self._format_event(e) for e in events]})

        if verb == "find_free_slot":
            duration = args.get("duration_minutes", 30)
            between = args.get("between", {})
            search_start = datetime.fromisoformat(between.get("start", now.isoformat()))
            search_end = datetime.fromisoformat(between.get("end", (now + timedelta(days=1)).isoformat()))

            events = await self._get_events(search_start.isoformat(), search_end.isoformat())
            if events is None:
                return ToolResult(success=False, error="Failed to read calendar")

            busy = []
            for e in events:
                start_str = e.get("start", {}).get("dateTime")
                end_str = e.get("end", {}).get("dateTime")
                if start_str and end_str:
                    busy.append((datetime.fromisoformat(start_str), datetime.fromisoformat(end_str)))
            busy.sort()

            cursor = search_start
            for b_start, b_end in busy:
                if (b_start - cursor).total_seconds() >= duration * 60:
                    return ToolResult(success=True, data={
                        "slot_start": cursor.isoformat(),
                        "slot_end": (cursor + timedelta(minutes=duration)).isoformat(),
                    })
                cursor = max(cursor, b_end)

            if (search_end - cursor).total_seconds() >= duration * 60:
                return ToolResult(success=True, data={
                    "slot_start": cursor.isoformat(),
                    "slot_end": (cursor + timedelta(minutes=duration)).isoformat(),
                })

            return ToolResult(success=True, data={"slot_start": None, "message": "No free slot found"})

        if verb == "add_event":
            hdrs = await self._headers()
            if not hdrs:
                return ToolResult(success=False, error="No Google token")
            from chloe.config import get_settings
            tz_str = getattr(get_settings(), "chloe_timezone", "UTC")
            event_body = {
                "summary": args["title"],
                "start": {"dateTime": args["start"], "timeZone": tz_str},
                "end": {"dateTime": args["end"], "timeZone": tz_str},
                "description": args.get("description", "Added by Chloe"),
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                    headers={**hdrs, "Content-Type": "application/json"},
                    json=event_body,
                )
            if resp.status_code == 401:
                new_token = await refresh_token("google")
                if new_token:
                    hdrs = {"Authorization": f"Bearer {new_token['access_token']}", "Content-Type": "application/json"}
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(
                            f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                            headers=hdrs,
                            json=event_body,
                        )
            if resp.status_code in (200, 201):
                event_id = resp.json().get("id", "")
                _register_artifact("calendar_event", event_id, args["title"], args.get("__action_id") or None)
                return ToolResult(
                    success=True,
                    data={"eventId": event_id, "title": args["title"]},
                    artifact_ref=event_id,
                    artifact_kind="calendar_event",
                )
            return ToolResult(success=False, error=f"Calendar API error: {resp.status_code}")

        if verb == "add_reminder":
            hdrs = await self._headers()
            if not hdrs:
                return ToolResult(success=False, error="No Google token")
            from chloe.config import get_settings
            tz_str = getattr(get_settings(), "chloe_timezone", "UTC")
            event_body = {
                "summary": args["body"],
                "start": {"dateTime": args["time"], "timeZone": tz_str},
                "end": {"dateTime": args["time"], "timeZone": tz_str},
                "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]},
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events",
                    headers={**hdrs, "Content-Type": "application/json"},
                    json=event_body,
                )
            if resp.status_code in (200, 201):
                event_id = resp.json().get("id", "")
                return ToolResult(success=True, data={"eventId": event_id, "body": args["body"]})
            return ToolResult(success=False, error=f"Calendar API error: {resp.status_code}")

        if verb == "decline":
            hdrs = await self._headers()
            if not hdrs:
                return ToolResult(success=False, error="No Google token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events/{args['eventId']}",
                    headers={**hdrs, "Content-Type": "application/json"},
                    json={"status": "declined"},
                )
            if resp.status_code in (200, 204):
                return ToolResult(success=True, data={"declined": args["eventId"]})
            return ToolResult(success=False, error=f"Calendar API error: {resp.status_code}")

        if verb == "delete_event":
            hdrs = await self._headers()
            if not hdrs:
                return ToolResult(success=False, error="No Google token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{CALENDAR_API}/calendars/{CALENDAR_ID}/events/{args['eventId']}",
                    headers=hdrs,
                )
            if resp.status_code in (200, 204):
                from chloe.state.db import get_connection
                conn = get_connection()
                conn.execute("UPDATE artifact_index SET exists_=0 WHERE ref=?", (args["eventId"],))
                conn.commit()
                return ToolResult(success=True, data={"deleted": args["eventId"]})
            return ToolResult(success=False, error=f"Calendar API error: {resp.status_code}")

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
