# D-05 · `initiative/candidates.py` — interest-driven candidates

## Overview

Implement `interest_driven_candidates(garden) -> list[CandidateAction]`. Top-3 interests each contribute one low-pressure candidate. Candidate `pressure` is `interest.intensity * 0.3` — always lower than pressure-driven candidates, ensuring interests yield to unmet needs.

## Context

Chloe has an `interest_garden` table tracking her intellectual and aesthetic interests with decay-adjusted intensities. Interest-driven candidates represent her proactive curiosity — she might search for new marine biology papers, add a music discovery to a note, or add a track to her queue based on a music interest. These are always low-priority so they don't crowd out higher-pressure candidates.

## Interest-to-tool mapping

```python
# chloe/initiative/candidates.py  (add interest_driven_candidates)

INTEREST_TOOL_MAP = {
    "research":   ("web_search", "search",   lambda i: {"query": f"{i['label']} recent discoveries"}),
    "science":    ("web_search", "search",   lambda i: {"query": f"{i['label']} latest research"}),
    "music":      ("spotify",    "like",      lambda i: {}),
    "writing":    ("notes",      "append",    lambda i: {"path": f"interests/{i['label'].replace(' ', '_')}.md", "text": ""}),
    "art":        ("notes",      "create",   lambda i: {"path": f"interests/art_{i['label'][:20]}.md", "text": ""}),
    "curiosity":  ("web_search", "search",   lambda i: {"query": i["label"]}),
}
# Default for unmapped interests
INTEREST_FALLBACK = ("web_search", "search", lambda i: {"query": i["label"]})
```

## Implementation

```python
def interest_driven_candidates(garden: list | None = None) -> list[CandidateAction]:
    """
    Build low-pressure candidate actions from top-3 highest-intensity interests.
    garden: optional override list; if None, reads from interest_garden table.
    """
    if garden is None:
        garden = _load_interests()

    # Sort by intensity DESC, take top 3
    top3 = sorted(garden, key=lambda i: i.get("intensity", 0.0), reverse=True)[:3]

    candidates = []
    for interest in top3:
        intensity = interest.get("intensity", 0.0)
        pressure = intensity * 0.3  # Cap at 0.3 — always lower than pressure-driven

        # Find matching tool mapping
        category = interest.get("category", "")
        tool, verb, args_factory = INTEREST_TOOL_MAP.get(category, INTEREST_FALLBACK)
        args = args_factory(interest)

        candidates.append(CandidateAction(
            tool=tool, verb=verb, args=args,
            intent=f"Explore my interest in {interest.get('label', 'this topic')}",
            pressure=pressure,
            source="interest",
            source_id=interest.get("id", ""),
        ))

    log.debug("interest_candidates", count=len(candidates), top_labels=[i.get("label") for i in top3])
    return candidates


def _load_interests() -> list[dict]:
    from chloe.state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM interest_garden WHERE intensity > 0.1 ORDER BY intensity DESC LIMIT 10"
    ).fetchall()
    return [dict(r) for r in rows]
```

## Dependencies

- F-04 (`interest_garden` table — `id`, `label`, `category`, `intensity`).
- D-03 (`CandidateAction` dataclass).

## Testing

### Unit tests — `tests/unit/test_interest_candidates.py`

```python
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
```

## Acceptance criteria

- `interest.label="marine biology", intensity=0.7` → `CandidateAction(tool="web_search")` with `pressure≈0.21`.
- Music interest → `tool="spotify"`, writing interest → `tool="notes"`.
- Only top-3 interests generate candidates (not all).
- `source="interest"`, `source_id` matches the interest `id`.
- Empty garden → `[]`.
