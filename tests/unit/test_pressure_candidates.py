import pytest
from chloe.initiative.candidates import pressure_driven_candidates, CandidateAction


def test_loneliness_want_produces_messages_candidate():
    inner_state = {
        "wants": [{"id": "w1", "pressure": 0.8, "tags": ["loneliness"], "description": "feel lonely"}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert any(c.tool == "messages" for c in candidates)
    assert any(c.pressure == 0.8 for c in candidates)


def test_curiosity_want_produces_web_search_and_notes():
    inner_state = {
        "wants": [{"id": "w2", "pressure": 0.7, "tags": ["curiosity"]}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    tools = {c.tool for c in candidates}
    assert "web_search" in tools
    assert "notes" in tools


def test_low_pressure_want_excluded():
    inner_state = {
        "wants": [{"id": "w3", "pressure": 0.4, "tags": ["loneliness"]}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert len(candidates) == 0


def test_fear_irrelevance_produces_message():
    inner_state = {
        "wants": [],
        "fears": [{"id": "f1", "pressure": 0.75, "tags": ["irrelevance"]}],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert any(c.tool == "messages" for c in candidates)


def test_high_tension_produces_notes_candidate():
    inner_state = {
        "wants": [],
        "fears": [],
        "tensions": [{"id": "t1", "pressure": 0.6, "description": "unresolved conflict"}],
    }
    candidates = pressure_driven_candidates(inner_state)
    assert any(c.tool == "notes" for c in candidates)


def test_source_field_is_pressure():
    inner_state = {
        "wants": [{"id": "w4", "pressure": 0.9, "tags": ["music"]}],
        "fears": [],
        "tensions": [],
    }
    candidates = pressure_driven_candidates(inner_state)
    for c in candidates:
        assert c.source == "pressure"
        assert c.source_id == "w4"


def test_empty_inner_state_returns_no_candidates():
    candidates = pressure_driven_candidates({"wants": [], "fears": [], "tensions": []})
    assert candidates == []
