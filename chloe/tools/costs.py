from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolCostEstimate:
    usd: float
    breakdown: dict[str, float]


TOOL_COSTS: dict[str, dict[str, ToolCostEstimate]] = {
    "notes": {
        "append": ToolCostEstimate(usd=0.001, breakdown={"gemini_flash": 0.001}),
        "search": ToolCostEstimate(usd=0.002, breakdown={"gemini_flash": 0.002}),
        "revert": ToolCostEstimate(usd=0.0005, breakdown={}),
    },
    "web_search": {
        "search": ToolCostEstimate(usd=0.005, breakdown={"serp_api": 0.005}),
    },
    "spotify": {
        "play_track": ToolCostEstimate(usd=0.0, breakdown={}),
        "add_to_queue": ToolCostEstimate(usd=0.0, breakdown={}),
        "play_playlist": ToolCostEstimate(usd=0.0, breakdown={}),
        "set_volume": ToolCostEstimate(usd=0.0, breakdown={}),
        "pause": ToolCostEstimate(usd=0.0, breakdown={}),
        "resume": ToolCostEstimate(usd=0.0, breakdown={}),
        "skip": ToolCostEstimate(usd=0.0, breakdown={}),
        "clear_queue": ToolCostEstimate(usd=0.0, breakdown={}),
    },
    "gmail": {
        "list_threads": ToolCostEstimate(usd=0.001, breakdown={"gemini_flash": 0.001}),
        "read_thread": ToolCostEstimate(usd=0.003, breakdown={"gemini_flash": 0.003}),
        "draft_reply": ToolCostEstimate(usd=0.008, breakdown={"gemini_flash": 0.008}),
        "send_reply": ToolCostEstimate(usd=0.008, breakdown={"gemini_flash": 0.008}),
    },
    "calendar": {
        "list_events": ToolCostEstimate(usd=0.001, breakdown={}),
        "add_reminder": ToolCostEstimate(usd=0.002, breakdown={"gemini_flash": 0.002}),
        "delete_event": ToolCostEstimate(usd=0.0005, breakdown={}),
    },
    "smart_home": {
        "lights": ToolCostEstimate(usd=0.0, breakdown={}),
        "thermostat": ToolCostEstimate(usd=0.0, breakdown={}),
        "media_player": ToolCostEstimate(usd=0.0, breakdown={}),
        "scene": ToolCostEstimate(usd=0.0, breakdown={}),
    },
    "messages": {
        "send_text": ToolCostEstimate(usd=0.001, breakdown={"push_apns": 0.0, "gemini_flash": 0.001}),
    },
    "weather": {
        "current": ToolCostEstimate(usd=0.0, breakdown={"open_meteo": 0.0}),
        "forecast": ToolCostEstimate(usd=0.0, breakdown={"open_meteo": 0.0}),
    },
    "maps": {
        "find_place": ToolCostEstimate(usd=0.005, breakdown={"google_maps": 0.005}),
        "directions": ToolCostEstimate(usd=0.01, breakdown={"google_maps": 0.01}),
        "traffic_to": ToolCostEstimate(usd=0.01, breakdown={"google_maps": 0.01}),
        "commute_estimate": ToolCostEstimate(usd=0.01, breakdown={"google_maps": 0.01}),
    },
    "self_tools": {
        "set_quiet": ToolCostEstimate(usd=0.0, breakdown={}),
        "set_focus": ToolCostEstimate(usd=0.0, breakdown={}),
        "add_goal": ToolCostEstimate(usd=0.0, breakdown={}),
        "add_want": ToolCostEstimate(usd=0.0, breakdown={}),
        "update_preference": ToolCostEstimate(usd=0.0, breakdown={}),
        "archive_trait": ToolCostEstimate(usd=0.0, breakdown={}),
    },
}

_DEFAULT_COST = ToolCostEstimate(usd=0.002, breakdown={"default": 0.002})


def get_cost_estimate(tool: str, verb: str) -> ToolCostEstimate:
    return TOOL_COSTS.get(tool, {}).get(verb, _DEFAULT_COST)
