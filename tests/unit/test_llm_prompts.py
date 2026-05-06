"""Tests for LLM prompt rendering and live Gemini API integration."""
from __future__ import annotations

import json
import os
import re

import pytest

from chloe.llm.gemini import _render_prompt, GeminiClient
from chloe.llm.schemas import Verdict, OpportunityVector

_PLACEHOLDER_RE = re.compile(r"\{\{[\w.]+\}\}")

# ---------------------------------------------------------------------------
# Sample contexts
# ---------------------------------------------------------------------------

DELIBERATE_CONTEXT = {
    "proposed_action": {
        "tool": "messages",
        "verb": "send_text",
        "intent": "Check in with Teo — I want to connect",
        "preview": "Hey, how are you doing today?",
        "auth_class": "kinetic",
    },
    "recent_audit": "1. messages/send_text — 2 hours ago\n2. spotify/queue_track — 4 hours ago",
    "time_of_day": "09:15",
    "day_of_week": "Wednesday",
    "budget_throttle": "0.12",
    "last_chat_seen": "2 hours ago",
}

OPPORTUNITY_CONTEXT = {
    "time_of_day": "08:30",
    "day_of_week": "Tuesday",
    "calendar_events_today": "10:00 Team standup, 14:00 1:1 with manager",
    "last_chat_seen": "4 hours ago",
    "spotify_playing": "Nothing",
}


# ---------------------------------------------------------------------------
# Prompt rendering (no API key needed)
# ---------------------------------------------------------------------------

def test_deliberate_prompt_renders_no_placeholders():
    rendered = _render_prompt("deliberate_action.md", DELIBERATE_CONTEXT)
    assert not _PLACEHOLDER_RE.search(rendered), (
        "Unreplaced placeholders in deliberate_action.md: "
        + str(_PLACEHOLDER_RE.findall(rendered))
    )


def test_deliberate_prompt_contains_context_values():
    rendered = _render_prompt("deliberate_action.md", DELIBERATE_CONTEXT)
    assert "messages" in rendered
    assert "send_text" in rendered
    assert "Wednesday" in rendered
    assert "0.12" in rendered


def test_opportunity_prompt_renders_no_placeholders():
    rendered = _render_prompt("opportunity_vector.md", OPPORTUNITY_CONTEXT)
    assert not _PLACEHOLDER_RE.search(rendered), (
        "Unreplaced placeholders in opportunity_vector.md: "
        + str(_PLACEHOLDER_RE.findall(rendered))
    )


def test_opportunity_prompt_contains_context_values():
    rendered = _render_prompt("opportunity_vector.md", OPPORTUNITY_CONTEXT)
    assert "08:30" in rendered
    assert "Tuesday" in rendered
    assert "4 hours ago" in rendered


def test_render_handles_missing_key_gracefully():
    """Missing keys should render as empty string, not crash."""
    rendered = _render_prompt("deliberate_action.md", {})
    assert not _PLACEHOLDER_RE.search(rendered)


# ---------------------------------------------------------------------------
# Schema round-trips (no API key needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision", ["proceed", "abort", "revise"])
def test_verdict_schema_roundtrip(decision):
    raw = {"decision": decision, "reason": "test reason"}
    v = Verdict(**raw)
    assert v.decision == decision
    assert v.reason == "test reason"
    assert json.loads(v.model_dump_json()) == raw


def test_opportunity_vector_schema_roundtrip():
    raw = {
        "messages": 0.8, "spotify": 0.5, "calendar": 0.3,
        "notes": 0.7, "web_search": 0.9, "gmail": 0.2, "reminders": 0.4,
    }
    ov = OpportunityVector(**raw)
    assert ov.messages == 0.8
    assert 0.0 <= ov.spotify <= 1.0


def test_opportunity_vector_rejects_out_of_range():
    with pytest.raises(Exception):
        OpportunityVector(
            messages=1.5, spotify=0.5, calendar=0.3,
            notes=0.7, web_search=0.9, gmail=0.2, reminders=0.4,
        )


# ---------------------------------------------------------------------------
# Live API tests (require GEMINI_API_KEY)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.asyncio
async def test_live_deliberate_returns_verdict():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")

    client = GeminiClient(api_key=key)
    result = await client.flash("deliberate_action.md", DELIBERATE_CONTEXT, schema=Verdict)

    assert result is not None, "flash() returned None — API call failed"
    verdict = Verdict(**result)
    assert verdict.decision in ("proceed", "abort", "revise")
    assert isinstance(verdict.reason, str) and len(verdict.reason) > 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_opportunity_returns_vector():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")

    client = GeminiClient(api_key=key)
    result = await client.flash("opportunity_vector.md", OPPORTUNITY_CONTEXT, schema=OpportunityVector)

    assert result is not None, "flash() returned None — API call failed"
    ov = OpportunityVector(**result)
    for field in ("messages", "spotify", "calendar", "notes", "web_search", "gmail", "reminders"):
        val = getattr(ov, field)
        assert 0.0 <= val <= 1.0, f"{field}={val} out of range"


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_deliberate_abort_on_hostile_context():
    """Budget at 0.95 should push model toward abort."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")

    hostile_context = {**DELIBERATE_CONTEXT, "budget_throttle": "0.95"}
    client = GeminiClient(api_key=key)
    result = await client.flash("deliberate_action.md", hostile_context, schema=Verdict)

    assert result is not None
    verdict = Verdict(**result)
    # We can't assert the exact decision, but the schema must be valid
    assert verdict.decision in ("proceed", "abort", "revise")
    assert verdict.reason
