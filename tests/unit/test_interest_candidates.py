import pytest
from chloe.initiative.candidates import interest_driven_candidates, CandidateAction


def _make_interest(label, category, intensity, id="i1", gen_level=0, why=None):
    """Build an interest dict matching the DB schema.

    category is embedded in the why field as '[category] ...' per the live format.
    """
    if why is None:
        why = f"[{category}] interest in {label}"
    return {"id": id, "label": label, "why": why, "intensity": intensity, "gen_level": gen_level}


def test_marine_biology_interest_produces_web_search():
    """Science interests use web_search once gen_level >= 2."""
    garden = [_make_interest("marine biology", "science", 0.7, gen_level=2)]
    candidates = interest_driven_candidates(garden)
    assert len(candidates) == 1
    assert candidates[0].tool == "web_search"


def test_marine_biology_low_gen_level_falls_back_to_notes():
    """Before gen_level 2, science interests capture privately via notes."""
    garden = [_make_interest("marine biology", "science", 0.7, gen_level=0)]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].tool == "notes"


def test_music_interest_produces_spotify():
    garden = [_make_interest("post-rock", "music", 0.8, gen_level=2)]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].tool == "spotify"
    assert candidates[0].verb == "queue_track"


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


def test_pressure_is_intensity_times_0_85():
    """Pressure formula is min(0.9, intensity * 0.85)."""
    garden = [_make_interest("marine biology", "science", 0.7)]
    candidates = interest_driven_candidates(garden)
    assert abs(candidates[0].pressure - 0.7 * 0.85) < 0.001


def test_pressure_capped_at_0_9():
    """Cap triggers when intensity * 0.85 > 0.9, i.e., intensity > ~1.06."""
    garden = [_make_interest("topic", "curiosity", 1.1)]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].pressure == 0.9


def test_source_is_interest():
    garden = [_make_interest("marine biology", "science", 0.7, id="int_1")]
    candidates = interest_driven_candidates(garden)
    assert candidates[0].source == "interest"
    assert candidates[0].source_id == "int_1"


def test_empty_garden_returns_empty():
    candidates = interest_driven_candidates([])
    assert candidates == []


def test_low_intensity_below_threshold_excluded():
    """Interests below 0.3 intensity are excluded."""
    garden = [_make_interest("topic", "curiosity", 0.15, id="low")]
    candidates = interest_driven_candidates(garden)
    assert len(candidates) == 0


def test_interest_at_threshold_included():
    """Interests at exactly 0.3 intensity are included."""
    garden = [_make_interest("topic", "curiosity", 0.30, id="at_threshold")]
    candidates = interest_driven_candidates(garden)
    assert len(candidates) == 1
