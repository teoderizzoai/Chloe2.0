# H-02 · Procedural memory injected into deliberation

## Overview

In `deliberate.py`, before building the Flash input pack, query `memory_store.query_mixed()` for `kind="procedural"` memories relevant to the proposed action's `tool/verb`. Inject top-3 as `procedural_hits` in the deliberation prompt.

## Context

H-01 distills feedback into procedural rules. H-02 closes the loop: those rules must reach the deliberation call that decides whether to proceed. Without this step, Chloe generates procedural memories but never consults them. With it, a past pattern of reverted calendar actions produces a rule that appears in the next deliberation for `calendar.add_reminder`, shifting the verdict toward `abort` or `revise`.

## Implementation

### Update `deliberate()` to fetch procedural memories

```python
# In chloe/actions/deliberate.py — update deliberate():

from chloe.memory.retrieval import query_mixed
from chloe.observability.logging import get_logger

log = get_logger("deliberate")


async def deliberate(action: "Action", context: dict) -> "Verdict | None":
    # Fetch procedural memories relevant to this action
    procedural_hits = await _get_procedural_memories(action)

    input_pack = _build_input_pack(action, context, procedural_hits=procedural_hits)

    use_pro = _is_kinetic_sensitive(action) and _high_cost_estimate(action)

    try:
        if use_pro:
            result = await _gemini.pro_thinking(
                prompt_name="deliberation.md",
                payload=input_pack,
                schema=Verdict,
                thinking_budget=DELIBERATION_THINKING_BUDGET,
            )
        else:
            result = await _gemini.flash(
                prompt_name="deliberation.md",
                payload=input_pack,
                schema=Verdict,
            )
    except Exception as exc:
        log.warning("deliberate_llm_error", error=str(exc))
        return None

    if result is None:
        return None

    return Verdict(**result) if isinstance(result, dict) else result


async def _get_procedural_memories(action: "Action") -> list[dict]:
    """
    Query top-3 procedural memories relevant to this action's tool/verb.
    Returns list of dicts with 'content' and 'confidence' fields.
    """
    query = f"{action.tool} {action.verb} {action.intent or ''}"
    try:
        memories = await query_mixed(
            rich_q=query,
            kinds_mix={"procedural": 3},   # Only procedural, top 3
        )
        return [
            {
                "content": m.content,
                "confidence": getattr(m, "weight", 0.7),
                "tags": m.tags,
            }
            for m in memories
        ]
    except Exception as exc:
        log.warning("procedural_retrieval_error", error=str(exc))
        return []
```

### Update `_build_input_pack()` to include procedural hits

```python
# In chloe/actions/deliberate.py:

def _build_input_pack(action: "Action", context: dict, procedural_hits: list[dict] | None = None) -> dict:
    from chloe.actions.audit import feed_text
    from datetime import datetime

    now = datetime.utcnow()
    return {
        "proposed_action": {
            "tool": action.tool,
            "verb": action.verb,
            "args": action.args,
            "intent": action.intent,
            "auth_class": action.auth_class,
            "cost_usd": action.cost_usd,
        },
        "procedural_hits": procedural_hits or [],     # H-02: inject here
        "recent_audit": feed_text(limit=10),
        "budget_throttle": context.get("budget_throttle", 0.0),
        "time_of_day": now.strftime("%H:%M"),
        "day_of_week": now.strftime("%A"),
        "last_chat_seen": context.get("last_chat_seen", ""),
    }
```

### Update deliberation prompt

```markdown
<!-- chloe/prompts/deliberation.md -->
You are deliberating on whether Chloe should execute a proposed action.

## Proposed action:
Tool: {{proposed_action.tool}}
Verb: {{proposed_action.verb}}
Args: {{proposed_action.args}}
Intent: {{proposed_action.intent}}
Authorization: {{proposed_action.auth_class}}
Estimated cost: ${{proposed_action.cost_usd}}

## Past procedural rules (from learned patterns):
{% if procedural_hits %}
{% for hit in procedural_hits %}
- [confidence: {{hit.confidence}}] {{hit.content}}
{% endfor %}
{% else %}
(No relevant procedural rules found.)
{% endif %}

## Recent actions (last 10):
{{recent_audit}}

## Context:
- Time: {{time_of_day}} on {{day_of_week}}
- Budget throttle: {{budget_throttle}}
- Last chat: {{last_chat_seen}}

## Decision:
Return a Verdict with decision one of: "proceed", "abort", "revise".
- "proceed": execute the action as proposed
- "abort": do not execute; reason must be specific
- "revise": execute with modified args (provide revised_args)

If procedural_hits warn against this action or context, weight them heavily.
```

## Testing

### Unit tests — `tests/unit/test_procedural_in_deliberation.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.actions.deliberate import deliberate, _get_procedural_memories, _build_input_pack


class MockAction:
    def __init__(self):
        self.id = "test_id"
        self.tool = "calendar"
        self.verb = "add_reminder"
        self.args = {"title": "Meeting", "time": "08:00"}
        self.intent = "Add morning meeting"
        self.auth_class = "kinetic"
        self.cost_estimate = None

    @property
    def cost_usd(self):
        return 0.0


class MockMemory:
    def __init__(self, content, weight=0.7):
        self.content = content
        self.weight = weight
        self.tags = ["procedural", "calendar"]


@pytest.mark.asyncio
async def test_procedural_hits_included_in_input_pack():
    action = MockAction()
    procedural_hits = [
        {"content": "Avoid calendar events before 9am", "confidence": 0.8, "tags": ["calendar"]}
    ]
    pack = _build_input_pack(action, {}, procedural_hits=procedural_hits)

    assert "procedural_hits" in pack
    assert len(pack["procedural_hits"]) == 1
    assert "before 9am" in pack["procedural_hits"][0]["content"]


@pytest.mark.asyncio
async def test_no_procedural_memories_gives_empty_list():
    action = MockAction()
    pack = _build_input_pack(action, {}, procedural_hits=None)
    assert pack["procedural_hits"] == []


@pytest.mark.asyncio
async def test_get_procedural_memories_queries_kind():
    action = MockAction()
    mock_memories = [MockMemory("Avoid early morning calendar events")]

    with patch("chloe.actions.deliberate.query_mixed", new=AsyncMock(return_value=mock_memories)) as mock_q:
        hits = await _get_procedural_memories(action)

    mock_q.assert_awaited_once()
    call_kwargs = mock_q.call_args.kwargs
    assert call_kwargs.get("kinds_mix", {}).get("procedural") == 3
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_get_procedural_memories_error_returns_empty():
    action = MockAction()

    with patch("chloe.actions.deliberate.query_mixed", new=AsyncMock(side_effect=Exception("ChromaDB down"))):
        hits = await _get_procedural_memories(action)

    assert hits == []


@pytest.mark.asyncio
async def test_deliberate_receives_procedural_context():
    """Full deliberate() path — procedural hits flow through to Flash."""
    action = MockAction()
    context = {}

    mock_memories = [MockMemory("Do not add calendar events before 9am")]
    captured_payloads = []

    async def mock_flash(prompt_name, payload, schema):
        captured_payloads.append(payload)
        return {"decision": "abort", "rationale": "Rule violation: before 9am"}

    with patch("chloe.actions.deliberate.query_mixed", new=AsyncMock(return_value=mock_memories)):
        with patch("chloe.actions.deliberate._gemini") as mock_gemini:
            mock_gemini.flash = mock_flash
            result = await deliberate(action, context)

    assert result is not None
    assert len(captured_payloads) == 1
    assert len(captured_payloads[0]["procedural_hits"]) == 1
    assert "9am" in captured_payloads[0]["procedural_hits"][0]["content"]


@pytest.mark.asyncio
async def test_integration_procedural_rule_in_deliberation(tmp_path):
    """
    Integration: after H-01 generates a calendar rule, next calendar.add_reminder
    deliberation receives that rule.
    """
    from pathlib import Path
    from chloe.state.db import migrate, close, get_connection

    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection()

    # Simulate H-01 storing a procedural memory
    import ulid
    memory_id = str(ulid.new())
    conn.execute(
        """
        INSERT INTO memories (id, kind, content, tags, source, weight, archived_tier, artifact_refs)
        VALUES (?, 'procedural', ?, ?, 'distillation', 0.8, 'hot', '[]')
        """,
        (memory_id, "Do not add calendar events before 9am on weekdays.",
         json.dumps(["procedural", "calendar"]))
    )
    conn.commit()

    action = MockAction()
    hits = await _get_procedural_memories(action)

    # Should retrieve the stored procedural memory
    assert len(hits) >= 1
    assert any("calendar" in h.get("content", "") or "9am" in h.get("content", "") for h in hits)
    close()
```

## Dependencies

- H-01 (`procedural.py` — produces memories this step retrieves).
- E-06 (`memory/retrieval.py` — `query_mixed()` with procedural kind support).
- D-01 (`deliberate.py` — Flash input pack structure).

## Acceptance criteria

- `deliberate()` calls `_get_procedural_memories()` for every action.
- Retrieved memories appear in `input_pack["procedural_hits"]`.
- Flash/pro_thinking receives `procedural_hits` in the prompt payload.
- `query_mixed` is called with `kinds_mix={"procedural": 3}`.
- Retrieval error → empty list, deliberation continues normally.
- Integration test: stored procedural rule for `calendar` appears in `_get_procedural_memories()` result for a `calendar.add_reminder` action.
