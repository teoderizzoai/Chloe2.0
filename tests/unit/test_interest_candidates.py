import pytest
from chloe.initiative.candidates import interest_driven_candidates, CandidateAction


def _make_interest(label, category, intensity, id="i1"):
    return {"id": id, "label": label, "category": category, "intensity": intensity}


def test_marine_biology_interest_produces_web_search():
    garden = [_make_interest("marine biology", "science", 0.7)]
    candidates = interest_driven_candidates(garden)
    assert len(candidates) == 1
    assert candidates[0].tool == "web_search"
    assert abs(candidates[0].pressure - 0.7 * 0.3) < 0.001


def test_music_interest_produces_spotify():
    garden = [_make_interest("post-rock", "music", 0.8)]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].tool == "spotify"
    assert candidates[0].verb == "like"


def test_writing_interest_produces_notes():
    garden = [_make_interest("creative writing", "writing", 0.6)]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].tool == "notes"


def test_top_3_only():
    garden = [
        _make_interest(f"topic_{i}", "science", 0.9 - i * 0.1, id=f"i{i}")
        for i in range(6)
    ]
    candidates = interest_driven_candidates(garden)
    assert len(candidates) == 3


def test_pressure_is_intensity_times_0_3():
    garden = [_make_interest("marine biology", "science", 0.7)]
    candidates = interest_driven_candidates(garden)
    assert abs(candidates[0].pressure - 0.21) < 0.001


def test_source_is_interest():
    garden = [_make_interest("marine biology", "science", 0.7, id="int_1")]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].source == "interest"
    assert candidates[0].source_id == "int_1"


def test_empty_garden_returns_empty():
    candidates = interest_driven_candidates([])
    assert candidates == []


def test_low_intensity_interest_included_if_top_3():
    garden = [_make_interest("topic", "curiosity", 0.15, id="low")]
    candidates = interest_driven_candidates(garden)
    assert len(candidates) == 1
    assert abs(candidates[0].pressure - 0.15 * 0.3) < 0.001
