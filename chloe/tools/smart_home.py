from __future__ import annotations

import json

import httpx

from chloe.config import get_settings
from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.smart_home")

HA_SERVICES_URL = "{base}/api/services/{domain}/{service}"


class SmartHomeTool(Tool):
    name = "smart_home"

    def __init__(self):
        self.verbs = {
            "lights": ToolVerb(
                name="lights",
                schema={
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "HA entity ID, e.g. light.living_room"},
                        "state": {"type": "string", "enum": ["on", "off"], "description": "Desired light state"},
                        "brightness": {"type": "integer", "minimum": 0, "maximum": 255},
                        "color_temp": {"type": "integer", "description": "Color temperature in mireds"},
                    },
                    "required": ["entity", "state"],
                },
                auth_class="kinetic-sensitive",
                reversibility=0.9,
                description_for_model="Turn a light on or off, optionally setting brightness and color temperature.",
                description_for_human="Control smart lights",
            ),
            "thermostat": ToolVerb(
                name="thermostat",
                schema={
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "HA climate entity ID"},
                        "temperature": {"type": "number", "description": "Target temperature in Celsius"},
                        "hvac_mode": {
                            "type": "string",
                            "enum": ["heat", "cool", "heat_cool", "off", "auto"],
                        },
                    },
                    "required": ["entity", "temperature"],
                },
                auth_class="kinetic-sensitive",
                reversibility=0.8,
                description_for_model="Set thermostat temperature and optionally HVAC mode.",
                description_for_human="Adjust thermostat",
            ),
            "media_player": ToolVerb(
                name="media_player",
                schema={
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "HA media_player entity ID"},
                        "op": {
                            "type": "string",
                            "enum": ["play", "pause", "stop", "volume_up", "volume_down", "mute"],
                        },
                    },
                    "required": ["entity", "op"],
                },
                auth_class="kinetic",
                reversibility=0.95,
                description_for_model="Control a media player (play, pause, stop, volume).",
                description_for_human="Control media player",
            ),
            "scene": ToolVerb(
                name="scene",
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "HA scene entity ID, e.g. scene.movie_night"},
                    },
                    "required": ["name"],
                },
                auth_class="kinetic-sensitive",
                reversibility=0.5,
                description_for_model="Activate a HomeAssistant scene (preset collection of device states).",
                description_for_human="Activate HA scene",
            ),
        }

    def _headers(self) -> dict:
        settings = get_settings()
        return {
            "Authorization": f"Bearer {settings.ha_token}",
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return get_settings().ha_base_url

    async def _check_blocklist(self, entity: str) -> bool:
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM preferences WHERE key='ha_blocklist'"
        ).fetchone()
        if not row:
            return False
        blocked = json.loads(row["value"])
        return entity in blocked

    async def _check_allowlist(self, entity: str) -> bool:
        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM preferences WHERE key='ha_allowlist'"
        ).fetchone()
        if not row:
            return True
        allowed = json.loads(row["value"])
        if not allowed:
            return True  # empty allowlist means unrestricted
        return entity in allowed

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "lights":
            return await self._lights(args)
        elif verb == "thermostat":
            return await self._thermostat(args)
        elif verb == "media_player":
            return await self._media_player(args)
        elif verb == "scene":
            return await self._scene(args)
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _lights(self, args: dict) -> ToolResult:
        entity = args.get("entity", "")
        state = args.get("state", "")

        if await self._check_blocklist(entity):
            return ToolResult(success=False, error=f"Entity {entity!r} is on the HA blocklist")
        if not await self._check_allowlist(entity):
            return ToolResult(success=False, error=f"Entity {entity!r} is not on the HA allowlist")

        service = "turn_on" if state == "on" else "turn_off"
        payload: dict = {"entity_id": entity}
        if args.get("brightness") is not None:
            payload["brightness"] = args["brightness"]
        if args.get("color_temp") is not None:
            payload["color_temp"] = args["color_temp"]

        return await self._call_service("light", service, payload)

    async def _thermostat(self, args: dict) -> ToolResult:
        entity = args.get("entity", "")

        if await self._check_blocklist(entity):
            return ToolResult(success=False, error=f"Entity {entity!r} is on the HA blocklist")
        if not await self._check_allowlist(entity):
            return ToolResult(success=False, error=f"Entity {entity!r} is not on the HA allowlist")

        payload: dict = {"entity_id": entity, "temperature": args.get("temperature")}
        if args.get("hvac_mode"):
            payload["hvac_mode"] = args["hvac_mode"]

        result = await self._call_service("climate", "set_temperature", payload)
        if result.success and args.get("hvac_mode"):
            await self._call_service(
                "climate", "set_hvac_mode",
                {"entity_id": entity, "hvac_mode": args["hvac_mode"]},
            )
        return result

    async def _media_player(self, args: dict) -> ToolResult:
        entity = args.get("entity", "")
        op = args.get("op", "")

        if await self._check_blocklist(entity):
            return ToolResult(success=False, error=f"Entity {entity!r} is on the HA blocklist")

        op_map = {
            "play": ("media_player", "media_play"),
            "pause": ("media_player", "media_pause"),
            "stop": ("media_player", "media_stop"),
            "volume_up": ("media_player", "volume_up"),
            "volume_down": ("media_player", "volume_down"),
            "mute": ("media_player", "volume_mute"),
        }
        if op not in op_map:
            return ToolResult(success=False, error=f"Unknown media_player op: {op!r}")

        domain, service = op_map[op]
        payload: dict = {"entity_id": entity}
        if op == "mute":
            payload["is_volume_muted"] = True

        return await self._call_service(domain, service, payload)

    async def _scene(self, args: dict) -> ToolResult:
        name = args.get("name", "")

        if await self._check_blocklist(name):
            return ToolResult(success=False, error=f"Scene {name!r} is on the HA blocklist")
        if not await self._check_allowlist(name):
            return ToolResult(success=False, error=f"Scene {name!r} is not on the HA allowlist")

        return await self._call_service("scene", "turn_on", {"entity_id": name})

    async def _call_service(self, domain: str, service: str, payload: dict) -> ToolResult:
        url = HA_SERVICES_URL.format(base=self._base_url(), domain=domain, service=service)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)

            if resp.status_code in (200, 201):
                return ToolResult(
                    success=True,
                    data={
                        "domain": domain,
                        "service": service,
                        "entity": payload.get("entity_id"),
                        "payload": payload,
                    },
                )
            return ToolResult(success=False, error=f"HA API error: {resp.status_code} — {resp.text[:200]}")
        except httpx.ConnectError as e:
            log.warning("ha_connect_error", error=str(e), url=url)
            return ToolResult(success=False, error=f"Cannot reach HomeAssistant: {e}")
        except Exception as e:
            log.error("ha_unexpected_error", error=str(e))
            return ToolResult(success=False, error=str(e))

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "lights":
            return f"Would turn {args.get('state', '?')} {args.get('entity', '?')}"
        elif verb == "thermostat":
            return f"Would set {args.get('entity', '?')} to {args.get('temperature', '?')}°C"
        elif verb == "media_player":
            return f"Would {args.get('op', '?')} {args.get('entity', '?')}"
        elif verb == "scene":
            return f"Would activate scene {args.get('name', '?')}"
        return super().dry_run(verb, args)
