import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.actions.deliberate import deliberate, _get_procedural_memories


class MockAction:
    def __init__(self):
        self.id = "test_id"
        self.tool = "calendar"
        self.verb = "add_reminder"
        self.args = {"title": "Meeting", "time": "08:00"}
        self.intent = "Add morning meeting"
        self.authorization = "kinetic"
        self.preview = "Add Meeting reminder"
        self.cost_estimate = None

    @property
    def cost_usd(self):
        return 0.0


class MockMemory:
    def __init__(self, text, weight=0.7):
        self.text = text
        self.weight = weight
        self.tags = ["procedural", "calendar"]


def test_procedural_hits_included_in_payload():
    action = MockAction()
    mock_memories = [MockMemory("Avoid calendar events before 9am")]

    with patch("chloe.actions.deliberate.query_mixed", return_value=mock_memories):
        hits = _get_procedural_memories(action)

    assert len(hits) == 1
    assert "9am" in hits[0]["content"]


def test_no_procedural_memories_gives_empty_list():
    action = MockAction()

    with patch("chloe.actions.deliberate.query_mixed", return_value=[]):
        hits = _get_procedural_memories(action)

    assert hits == []


def test_get_procedural_memories_queries_kind():
    action = MockAction()
    mock_memories = [MockMemory("Avoid early morning calendar events")]

    with patch("chloe.actions.deliberate.query_mixed", return_value=mock_memories) as mock_q:
        hits = _get_procedural_memories(action)

    mock_q.assert_called_once()
    call_kwargs = mock_q.call_args.kwargs
    assert call_kwargs.get("kinds_mix", {}).get("procedural") == 3
    assert len(hits) == 1


def test_get_procedural_memories_error_returns_empty():
    action = MockAction()

    with patch("chloe.actions.deliberate.query_mixed", side_effect=Exception("ChromaDB down")):
        hits = _get_procedural_memories(action)

    assert hits == []


@pytest.mark.asyncio
async def test_deliberate_receives_procedural_context():
    action = MockAction()
    context = {}

    mock_memories = [MockMemory("Do not add calendar events before 9am")]
    captured_payloads = []

    async def mock_flash(prompt_file, context, schema):
        captured_payloads.append(context)
        return {"decision": "abort", "reason": "Rule violation: before 9am"}

    with patch("chloe.actions.deliberate.query_mixed", return_value=mock_memories):
        with patch("chloe.actions.deliberate.get_llm") as mock_get_llm:
            mock_llm = MagicMock()
            mock_llm.flash = mock_flash
            mock_get_llm.return_value = mock_llm
            with patch("chloe.actions.deliberate.audit_recent", new=AsyncMock(return_value=[])):
                result = await deliberate(action, context)

    assert result is not None
    assert len(captured_payloads) == 1
    assert len(captured_payloads[0]["procedural_hits"]) == 1
    assert "9am" in captured_payloads[0]["procedural_hits"][0]["content"]
