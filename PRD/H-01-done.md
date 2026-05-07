# H-01 · `memory/procedural.py` — weekly procedural distillation

## Overview

`distill_procedural()` runs weekly (Sundays at 03:00, alongside H-03). It queries all `(action, user_response)` pairs from the last 7 days where the user denied, reverted, or explicitly praised an action. Runs Flash on batches of up to 10 pairs. Each call returns `list[ProceduralRule]`. Stores rules as `Memory(kind="procedural", ...)`.

## Context

Chloe needs to learn from Teo's corrections without Teo having to repeat himself. When Teo reverts a calendar action three times, Chloe should remember not to do that kind of action in that context. Procedural memory encodes this as a rule ("when Teo has meetings before 10am, don't add calendar events before 9am") rather than storing raw episodes.

The batching approach (10 pairs per Flash call) keeps token costs reasonable and produces one distillation call per batch rather than one per event. The `user_praised` tag allows positive reinforcement — Chloe learns to repeat good patterns too, not just avoid bad ones.

## Implementation

### `memory/procedural.py`

```python
# chloe/memory/procedural.py
from __future__ import annotations
import json
from datetime import datetime, timedelta
from chloe.state.db import get_connection
from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import ProceduralRule
from chloe.memory.store import MemoryStore
from chloe.observability.logging import get_logger

log = get_logger("procedural")
_gemini = GeminiClient()
_memory_store = MemoryStore()

BATCH_SIZE = 10
MAX_BATCHES = 3


async def distill_procedural() -> list[str]:
    """
    Run weekly procedural distillation.
    Returns list of new Memory IDs created.
    """
    pairs = _load_feedback_pairs()
    if not pairs:
        log.info("procedural_no_pairs")
        return []

    batches = [pairs[i:i + BATCH_SIZE] for i in range(0, len(pairs), BATCH_SIZE)][:MAX_BATCHES]
    new_memory_ids = []

    for batch_idx, batch in enumerate(batches):
        rules = await _extract_rules_from_batch(batch, batch_idx)
        for rule in rules:
            memory_id = await _store_rule(rule)
            if memory_id:
                new_memory_ids.append(memory_id)
                log.info("procedural_rule_stored", memory_id=memory_id, tool=rule.tool)

    log.info("procedural_distillation_complete", new_rules=len(new_memory_ids))
    return new_memory_ids


def _load_feedback_pairs() -> list[dict]:
    """
    Load (action, user_response) pairs from the last 7 days.
    Includes: denied confirmations, reverted actions, user_praised tagged actions.
    """
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

    # Denied confirmations
    denied = conn.execute(
        """
        SELECT a.id, a.tool, a.verb, a.args, a.intent, a.created_at,
               'deny' as response_kind
        FROM actions a
        WHERE a.state = 'denied'
          AND a.created_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    # Reverted actions
    reverted = conn.execute(
        """
        SELECT a.id, a.tool, a.verb, a.args, a.intent, a.created_at,
               'revert' as response_kind
        FROM actions a
        WHERE a.state = 'reverted'
          AND a.created_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    # Praised actions (tagged in memories)
    praised = conn.execute(
        """
        SELECT a.id, a.tool, a.verb, a.args, a.intent, a.created_at,
               'praise' as response_kind
        FROM actions a
        JOIN memories m ON m.source_ref = a.id
        WHERE m.tags LIKE '%user_praised%'
          AND a.created_at >= ?
        """,
        (cutoff,),
    ).fetchall()

    pairs = []
    for row in [*denied, *reverted, *praised]:
        pairs.append({
            "action_id": row["id"],
            "tool": row["tool"],
            "verb": row["verb"],
            "args": json.loads(row["args"]) if isinstance(row["args"], str) else row["args"],
            "intent": row["intent"],
            "created_at": row["created_at"],
            "response_kind": row["response_kind"],
        })

    return pairs


async def _extract_rules_from_batch(batch: list[dict], batch_idx: int) -> list[ProceduralRule]:
    """Call Flash to extract procedural rules from a batch of feedback pairs."""
    payload = {
        "feedback_pairs": batch,
        "batch_index": batch_idx,
        "instructions": (
            "Analyze these action-feedback pairs. "
            "For each pattern you notice (same tool being denied/reverted repeatedly, "
            "or a type of action consistently praised), extract a concise procedural rule. "
            "Rules should be actionable: 'When X, do/avoid Y.' "
            "Include the tool name and be specific about context."
        ),
    }

    try:
        result = await _gemini.flash(
            prompt_name="procedural_distillation.md",
            payload=payload,
            schema=list[ProceduralRule],
        )
        if result is None:
            return []
        if isinstance(result, list):
            return [ProceduralRule(**r) if isinstance(r, dict) else r for r in result]
        return []
    except Exception as exc:
        log.warning("procedural_flash_error", error=str(exc), batch_idx=batch_idx)
        return []


async def _store_rule(rule: ProceduralRule) -> str | None:
    """Store a ProceduralRule as a procedural Memory. Returns memory_id."""
    from chloe.memory.models import Memory
    import ulid

    memory = Memory(
        id=str(ulid.new()),
        kind="procedural",
        content=rule.rule_text,
        tags=["procedural", rule.tool] + (rule.tags or []),
        source="distillation",
        weight=0.8,
        archived_tier="hot",
        artifact_refs=[],
    )

    try:
        await _memory_store.upsert(memory)
        return memory.id
    except Exception as exc:
        log.error("procedural_store_error", error=str(exc))
        return None
```

### `ProceduralRule` schema (confirm in F-06)

```python
# In chloe/llm/schemas.py (verify/update):
class ProceduralRule(BaseModel):
    rule_text: str = Field(max_length=500, description="Concise actionable rule: 'When X, do/avoid Y'")
    tool: str = Field(description="Primary tool this rule applies to")
    tags: list[str] = Field(default_factory=list, description="Additional tags for retrieval")
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    valence: str = Field(default="avoid", description="'avoid' or 'prefer'")
```

### Wire into weekly job

```python
# In chloe/loop.py — add to weekly Sunday tasks:

async def _run_weekly_jobs():
    from chloe.memory.procedural import distill_procedural
    from chloe.identity.self_model import run_weekly_self_model

    log.info("weekly_jobs_start")
    await distill_procedural()
    await run_weekly_self_model()
    log.info("weekly_jobs_complete")
```

### Prompt template

```markdown
<!-- chloe/prompts/procedural_distillation.md -->
You are analyzing a week of action-feedback pairs from an AI personal assistant.

Your job is to extract concise procedural rules that the assistant should follow in the future.

## Feedback pairs:
{{feedback_pairs}}

## Instructions:
{{instructions}}

Return a JSON array of ProceduralRule objects. Each rule should be:
- Specific: reference the tool and situation
- Actionable: starts with "When..." or "Always..." or "Never..."
- Evidence-based: grounded in the pairs provided

Return [] if no clear patterns emerge.
```

## Testing

### Unit tests — `tests/unit/test_procedural_distillation.py`

```python
import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.memory.procedural import distill_procedural, _load_feedback_pairs

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_action(db, tool, verb, state, days_ago=1):
    import ulid
    from datetime import datetime, timedelta
    action_id = str(ulid.new())
    created_at = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    db.execute(
        """
        INSERT INTO actions (id, tool, verb, args, intent, state, authorization, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (action_id, tool, verb, "{}", f"Test {verb}", state, "kinetic", created_at),
    )
    db.commit()
    return action_id


def test_load_feedback_pairs_includes_reverted(db):
    for _ in range(5):
        _insert_action(db, "calendar", "add_reminder", "reverted", days_ago=2)

    pairs = _load_feedback_pairs()
    calendar_pairs = [p for p in pairs if p["tool"] == "calendar"]
    assert len(calendar_pairs) == 5
    assert all(p["response_kind"] == "revert" for p in calendar_pairs)


def test_load_feedback_pairs_excludes_old_actions(db):
    _insert_action(db, "notes", "append", "reverted", days_ago=10)  # > 7 days
    _insert_action(db, "notes", "append", "reverted", days_ago=3)   # within 7 days

    pairs = _load_feedback_pairs()
    notes_pairs = [p for p in pairs if p["tool"] == "notes"]
    assert len(notes_pairs) == 1


@pytest.mark.asyncio
async def test_distill_procedural_creates_memories(db):
    for _ in range(5):
        _insert_action(db, "calendar", "add_reminder", "reverted", days_ago=1)

    mock_rule = {
        "rule_text": "When adding calendar reminders, avoid times before 9am.",
        "tool": "calendar",
        "tags": ["timing"],
        "confidence": 0.8,
        "valence": "avoid",
    }

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=[mock_rule])
        with patch("chloe.memory.procedural._memory_store") as mock_store:
            mock_store.upsert = AsyncMock()
            result = await distill_procedural()

    assert len(result) >= 1


@pytest.mark.asyncio
async def test_distill_procedural_at_least_one_calendar_rule(db):
    """5 reverted calendar actions → at least 1 calendar procedural rule."""
    for _ in range(5):
        _insert_action(db, "calendar", "add_reminder", "reverted", days_ago=1)

    stored_rules = []

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=[{
            "rule_text": "Avoid adding calendar reminders in early morning.",
            "tool": "calendar",
            "tags": [],
            "confidence": 0.75,
            "valence": "avoid",
        }])
        with patch("chloe.memory.procedural._memory_store") as mock_store:
            async def capture_upsert(memory):
                stored_rules.append(memory)
            mock_store.upsert = capture_upsert
            await distill_procedural()

    assert len(stored_rules) >= 1
    assert any("calendar" in r.tags for r in stored_rules)


@pytest.mark.asyncio
async def test_distill_procedural_empty_when_no_pairs(db):
    result = await distill_procedural()
    assert result == []


@pytest.mark.asyncio
async def test_flash_error_does_not_crash(db):
    _insert_action(db, "notes", "append", "reverted", days_ago=1)

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(side_effect=Exception("LLM error"))
        with patch("chloe.memory.procedural._memory_store") as mock_store:
            mock_store.upsert = AsyncMock()
            result = await distill_procedural()
    assert result == []  # No crash, no rules
```

## Dependencies

- F-05 (`llm/gemini.py` — Flash call).
- F-06 (`ProceduralRule` schema).
- E-06 (`memory/retrieval.py` — procedural kind in `query_mixed`).
- `actions` table (`state` field — denied/reverted).

## Acceptance criteria

- 5 reverted calendar actions in 7 days → at least 1 procedural memory created involving "calendar".
- Pairs older than 7 days excluded.
- Flash error → empty result, no exception raised.
- Each produced memory has `kind="procedural"` and contains the tool name in tags.
- Runs successfully as part of the Sunday weekly jobs batch.
