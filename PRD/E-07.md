# E-07 · Memory grader Flash call — `grade_memories.md`

## Overview

Implement `grade(candidates, message, history, affect, keep=5) -> list[Memory]`. Makes a Flash call using `grade_memories.md`. Returns the top-K most relevant memories from the candidate set with a per-memory relevance note. Updates the chat path to use this grader.

## Context

`query_mixed` (E-06) returns candidates by vector similarity. Similarity is a good first filter but a poor final ranker — a memory might be high-cosine-similarity to the query but actually irrelevant to the current conversational moment (e.g., a memory of a long-past event that matches by keywords but isn't contextually useful). The grader gives the LLM a chance to re-rank based on conversational relevance, affect alignment, and temporal appropriateness.

## Implementation

```python
# chloe/memory/retrieval.py — add grade() function

from chloe.llm.schemas import Graded
from typing import Any


async def grade(
    candidates: list[dict],
    message: str,
    history: list[dict],
    affect: dict,
    keep: int = 5,
) -> list[dict]:
    """
    Re-rank candidate memories using a Flash call for conversational relevance.
    Returns the top-keep memories with a 'relevance_note' field.
    """
    if not candidates:
        return []

    if len(candidates) <= keep:
        return candidates

    from chloe.llm.gemini import get_client as get_llm
    llm = get_llm()

    payload = {
        "message": message[:500],
        "recent_history": [{"role": t.get("role"), "text": t.get("text", "")[:100]}
                           for t in (history or [])[-5:]],
        "affect": {
            "valence": round(affect.get("valence", 0.0), 2),
            "arousal": round(affect.get("arousal", 0.5), 2),
        },
        "candidates": [
            {
                "index": i,
                "kind": m.get("kind", ""),
                "text": m.get("text", "")[:200],
                "weight": m.get("weight", 1.0),
            }
            for i, m in enumerate(candidates)
        ],
        "keep": keep,
    }

    result = await llm.flash("grade_memories.md", payload, schema=Graded)

    if result is None:
        log.warning("grade_llm_failed_using_top_k")
        return candidates[:keep]

    # Parse the graded result
    try:
        graded = Graded(**result) if isinstance(result, dict) else result
        selected_indices = graded.selected_indices[:keep]
        graded_memories = []
        for idx, note in zip(selected_indices, graded.relevance_notes or [""]*len(selected_indices)):
            if 0 <= idx < len(candidates):
                mem = dict(candidates[idx])
                mem["relevance_note"] = note
                graded_memories.append(mem)
        return graded_memories
    except Exception as exc:
        log.warning("grade_parse_error", error=str(exc))
        return candidates[:keep]
```

## `grade_memories.md` prompt template

```markdown
# Memory Grader

You are helping Chloe select the most relevant memories for the current conversation.

## Current context
- Message: {{message}}
- Recent history (last 5 turns): {{recent_history}}
- Chloe's current affect: valence={{affect.valence}}, arousal={{affect.arousal}}

## Candidate memories
{{candidates}}

## Task
Select the {{keep}} most relevant memories and briefly explain why each matters.
Consider: relevance to message, emotional resonance with current affect, temporal appropriateness.

Return JSON:
{
  "selected_indices": [0, 3, 7, ...],  // indices into the candidates list
  "relevance_notes": ["why #0 matters", "why #3 matters", ...]
}
```

## `Graded` schema (in `llm/schemas.py`)

```python
class Graded(BaseModel):
    selected_indices: list[int] = Field(max_length=20)
    relevance_notes: list[str] = Field(default=[])
```

## Update chat path

```python
# In channels/chat_api.py — replace simple retrieval with graded retrieval

from chloe.memory.retrieval import query_mixed, grade
from chloe.affect.dims import load as load_affect

async def get_graded_memories(message: str, history: list) -> list[dict]:
    affect = load_affect()
    candidates = query_mixed(message)
    graded = await grade(
        candidates=candidates,
        message=message,
        history=history,
        affect={"valence": affect.valence, "arousal": affect.arousal},
        keep=5,
    )
    return graded
```

## Dependencies

- E-06 (`memory/retrieval.py` — `query_mixed()` provides candidates).
- F-05 (`llm/gemini.py` — Flash call).
- F-06 (`llm/schemas.py` — `Graded` schema).
- E-03 (`affect/dims.py` — current affect state).

## Testing

### Unit tests — `tests/unit/test_grade_memories.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _make_candidate(idx, kind="episodic", text="some memory"):
    return {"id": f"mem_{idx}", "kind": kind, "text": text, "weight": 1.0, "_score": 0.5}


@pytest.mark.asyncio
async def test_grade_returns_keep_memories(monkeypatch):
    from chloe.memory.retrieval import grade

    mock_flash = AsyncMock(return_value={
        "selected_indices": [0, 2, 4, 6, 8],
        "relevance_notes": ["note1", "note2", "note3", "note4", "note5"],
    })
    monkeypatch.setattr("chloe.memory.retrieval.get_llm", lambda: MagicMock(flash=mock_flash))

    candidates = [_make_candidate(i) for i in range(20)]
    result = await grade(candidates, message="test message", history=[], affect={}, keep=5)

    assert len(result) == 5
    assert mock_flash.call_count == 1


@pytest.mark.asyncio
async def test_grade_fewer_than_keep_returns_all(monkeypatch):
    from chloe.memory.retrieval import grade

    mock_flash = AsyncMock()
    monkeypatch.setattr("chloe.memory.retrieval.get_llm", lambda: MagicMock(flash=mock_flash))

    candidates = [_make_candidate(i) for i in range(3)]
    result = await grade(candidates, message="test", history=[], affect={}, keep=5)

    assert len(result) == 3
    mock_flash.assert_not_called()  # No LLM needed when candidates < keep


@pytest.mark.asyncio
async def test_grade_llm_failure_returns_top_k(monkeypatch):
    from chloe.memory.retrieval import grade

    monkeypatch.setattr("chloe.memory.retrieval.get_llm", lambda: MagicMock(flash=AsyncMock(return_value=None)))

    candidates = [_make_candidate(i) for i in range(20)]
    result = await grade(candidates, message="test", history=[], affect={}, keep=5)

    assert len(result) == 5
    # Should fall back to first 5 candidates


@pytest.mark.asyncio
async def test_grade_adds_relevance_note(monkeypatch):
    from chloe.memory.retrieval import grade

    mock_flash = AsyncMock(return_value={
        "selected_indices": [0],
        "relevance_notes": ["directly relevant to current topic"],
    })
    monkeypatch.setattr("chloe.memory.retrieval.get_llm", lambda: MagicMock(flash=mock_flash))

    candidates = [_make_candidate(i) for i in range(10)]
    result = await grade(candidates, message="test", history=[], affect={}, keep=1)

    assert result[0]["relevance_note"] == "directly relevant to current topic"


@pytest.mark.asyncio
async def test_grade_empty_candidates_returns_empty(monkeypatch):
    from chloe.memory.retrieval import grade
    result = await grade([], message="test", history=[], affect={}, keep=5)
    assert result == []
```

## Acceptance criteria

- 20 candidates → `grade(..., keep=5)` returns exactly 5 memories.
- LLM failure → falls back to top-K by original score, no exception.
- Each returned memory has a `relevance_note` field.
- Fewer candidates than `keep` → return all without LLM call.
- Empty candidates → return `[]`.
