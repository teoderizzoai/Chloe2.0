"""E-05: tone_block() produces 1-3 lines; mood_label is gone."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from chloe.affect.dims import AffectState, tone_block


def test_positive_valence_warm_tone():
    s = AffectState(valence=0.5, arousal=0.4, social_pull=0.5, openness=0.6)
    result = tone_block(s)
    assert "warm" in result.lower() or "optimistic" in result.lower()


def test_negative_valence_subdued_tone():
    s = AffectState(valence=-0.5, arousal=0.4, social_pull=0.5, openness=0.6)
    result = tone_block(s)
    assert "subdued" in result.lower() or "introspective" in result.lower()


def test_high_arousal():
    s = AffectState(valence=0.0, arousal=0.9, social_pull=0.5, openness=0.6)
    result = tone_block(s)
    assert "energized" in result.lower() or "engaged" in result.lower()


def test_low_arousal():
    s = AffectState(valence=0.0, arousal=0.1, social_pull=0.5, openness=0.6)
    result = tone_block(s)
    assert "calm" in result.lower() or "unhurried" in result.lower()


def test_high_social_pull():
    s = AffectState(valence=0.0, arousal=0.4, social_pull=0.9, openness=0.6)
    result = tone_block(s)
    assert "connection" in result.lower()


def test_low_social_pull():
    s = AffectState(valence=0.0, arousal=0.4, social_pull=0.1, openness=0.6)
    result = tone_block(s)
    assert "space" in result.lower() or "quiet" in result.lower()


def test_neutral_state_returns_something():
    s = AffectState(valence=0.0, arousal=0.4, social_pull=0.5, openness=0.6)
    result = tone_block(s)
    assert len(result) > 0


def test_at_most_three_lines():
    s = AffectState(valence=0.5, arousal=0.8, social_pull=0.8, openness=0.6)
    result = tone_block(s)
    assert len(result.strip().splitlines()) <= 3


def test_mood_label_not_used_in_chat_api():
    """E-05: chat_api.py must not reference mood_label from kv."""
    chat_api_path = Path(__file__).parents[2] / "chloe/channels/chat_api.py"
    source = chat_api_path.read_text()
    assert "mood_label" not in source, \
        "chat_api.py still references mood_label — remove it (E-05)"
