# E-09 · Update chat path to use new memory retrieval

## Overview

In `channels/chat_api.py`, replace the old single-kind ChromaDB query with `memory_store.query_mixed()` + `memory_store.grade()`. Update the prompt assembly to use the graded memories with their `relevance_note` fields.

## Context

The chat path in 1.0 queried Chroma with a single query and took the top-5 results. Phase E's retrieval system adds quota-mixed retrieval (E-06) and LLM grading (E-07). This step wires them into the hot path of every chat turn. The affect state from E-03 feeds into the grader. The relevance notes from grading are not shown to the user but can be used as context in the prompt.

## Changes to `chat_api.py`

```python
# chloe/channels/chat_api.py

from chloe.memory.retrieval import query_mixed, grade
from chloe.affect.dims import load as load_affect, tone_block
from chloe.affect.label import get_label as get_affect_label

# Replace old memory retrieval function:

async def _get_relevant_memories(message: str, history: list) -> list[dict]:
    """Retrieve and grade memories relevant to the current message."""
    # 1. Vector retrieval with quota mix
    candidates = query_mixed(
        rich_q=message,
        kinds_mix={
            "episodic": 6,
            "semantic": 4,
            "autobiographical": 2,
            "procedural": 2,
        },
    )

    if not candidates:
        return []

    # 2. LLM re-ranking
    affect = load_affect()
    graded = await grade(
        candidates=candidates,
        message=message,
        history=history,
        affect={"valence": affect.valence, "arousal": affect.arousal},
        keep=5,
    )
    return graded


async def _build_memory_block(memories: list[dict]) -> str:
    """Format memories for injection into the system prompt."""
    if not memories:
        return ""
    lines = ["## Relevant memories"]
    for m in memories:
        kind_tag = f"[{m.get('kind', 'memory')}]"
        text = m.get("text", "")[:200]
        note = m.get("relevance_note", "")
        if note:
            lines.append(f"{kind_tag} {text}  ← {note}")
        else:
            lines.append(f"{kind_tag} {text}")
    return "\n".join(lines)


async def build_dynamic_suffix(person_id: str) -> str:
    """
    Build the per-turn dynamic suffix for the system prompt.
    Includes: audit feed, affect tone block, affect label.
    """
    from chloe.actions.audit import recent as audit_recent, feed_text
    from chloe.affect.label import get_label as get_affect_label

    recent = audit_recent(n=10)
    audit = feed_text(recent)
    affect = load_affect()
    tone = tone_block(affect)
    label = await get_affect_label(affect)

    parts = []
    if audit:
        parts.append(f"## Recent actions\n{audit}")
    if tone:
        parts.append(f"## Emotional state\n{tone}\n(Affect label: {label})")
    return "\n\n".join(parts)


async def chat_2_0(message: str, history: list, person_id: str = "teo") -> str:
    """
    Main chat handler for Chloe 2.0.
    Assembles memories, affect, audit context, calls Gemini Pro.
    """
    from chloe.llm.gemini import get_client as get_llm
    from chloe.state.kv import set as kv_set

    # Record last chat seen
    kv_set("last_chat_seen", __import__("datetime").datetime.utcnow().isoformat())

    # Retrieve and grade memories
    memories = await _get_relevant_memories(message, history)
    memory_block = await _build_memory_block(memories)

    # Build dynamic system suffix
    dynamic_suffix = await build_dynamic_suffix(person_id)

    # Assemble system instruction
    system_parts = []
    if memory_block:
        system_parts.append(memory_block)
    if dynamic_suffix:
        system_parts.append(dynamic_suffix)
    system_instruction = "\n\n".join(system_parts) if system_parts else None

    llm = get_llm()
    result = await llm.chat(
        contents=history + [{"role": "user", "parts": [message]}],
        system=system_instruction,
    )

    if result is None:
        return "I'm having a moment — please try again."

    return result.text
```

## Dependencies

- E-06 (`memory/retrieval.py` — `query_mixed()`).
- E-07 (`memory/retrieval.py` — `grade()`).
- E-03 (`affect/dims.py` — `load()`, `tone_block()`).
- E-04 (`affect/label.py` — `get_label()`).
- B-07 (`build_dynamic_suffix()` — extended here with affect data).

## Testing

### Integration tests — `tests/integration/test_chat_memory_retrieval.py`

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.mark.asyncio
async def test_action_memory_recalled_in_chat(db, monkeypatch):
    """After an action creates a memory (C-06 hook), the next chat recall should find it."""
    import json
    from chloe.actions.schema import ulid

    # Simulate a C-06 created memory
    action_id = ulid()
    memory_id = ulid()
    db.execute(
        "INSERT INTO memories (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (memory_id, "episodic", "I queued Radiohead track for Teo",
         "action", action_id, json.dumps([{"kind": "spotify_track", "ref": "spotify:track:abc"}]),
         1.0, '["action"]', "2026-05-04T10:00:00"),
    )
    db.commit()

    # Mock Chroma to return this memory
    mock_query_mixed = MagicMock(return_value=[{
        "id": memory_id, "kind": "episodic",
        "text": "I queued Radiohead track for Teo",
        "weight": 1.0, "_score": 0.8,
        "artifact_refs": [{"kind": "spotify_track", "ref": "spotify:track:abc"}],
    }])
    mock_grade = AsyncMock(return_value=[{
        "id": memory_id, "kind": "episodic",
        "text": "I queued Radiohead track for Teo",
        "weight": 1.0, "_score": 0.8, "relevance_note": "directly relevant",
        "artifact_refs": [],
    }])

    monkeypatch.setattr("chloe.channels.chat_api.query_mixed", mock_query_mixed)
    monkeypatch.setattr("chloe.channels.chat_api.grade", mock_grade)

    from chloe.channels.chat_api import _get_relevant_memories
    memories = await _get_relevant_memories("What music did you queue?", [])

    assert len(memories) == 1
    assert "Radiohead" in memories[0]["text"]


@pytest.mark.asyncio
async def test_build_dynamic_suffix_includes_audit_and_affect(db, monkeypatch):
    from chloe.actions.schema import Action
    from chloe.actions.audit import append as audit_append
    from chloe.channels.chat_api import build_dynamic_suffix

    # Add an audit entry
    action = Action(
        tool="spotify", verb="show_currently_playing", args={},
        intent="check what Teo is listening to",
        preview="Check Spotify",
        authorization="intimate",
        state="executed",
    )
    await audit_append(action)

    monkeypatch.setattr("chloe.channels.chat_api.get_affect_label", AsyncMock(return_value="calm and curious"))

    suffix = await build_dynamic_suffix("teo")
    assert "spotify" in suffix.lower()
    assert "calm and curious" in suffix.lower()
```

## Acceptance criteria

- Chat turn with `source="action"` memory in Chroma → `_get_relevant_memories()` returns that memory.
- `build_dynamic_suffix()` includes audit feed content and affect label.
- `grade()` LLM call receives current affect state.
- Memories injected into system prompt use the `## Relevant memories` block format.
- `kv["last_chat_seen"]` updated on each chat turn.
