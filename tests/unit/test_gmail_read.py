import pytest
from unittest.mock import AsyncMock, patch

from chloe.tools.gmail import GmailTool

MOCK_MESSAGES_LIST = {"messages": [{"id": "msg123", "threadId": "thread456"}]}
MOCK_MESSAGE_DETAIL = {
    "id": "msg123",
    "threadId": "thread456",
    "snippet": "Here is the latest update",
    "payload": {
        "headers": [
            {"name": "From", "value": "alice@example.com"},
            {"name": "Subject", "value": "Project update"},
            {"name": "Date", "value": "Mon, 4 May 2026 10:00:00 +0000"},
        ]
    },
}


@pytest.fixture
def tool():
    return GmailTool()


@pytest.mark.asyncio
async def test_read_recent_returns_messages(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get", side_effect=[MOCK_MESSAGES_LIST, MOCK_MESSAGE_DETAIL]):
        result = await tool.execute("read_recent", {"limit": 1})
    assert result.success
    assert len(result.data["messages"]) == 1
    assert result.data["messages"][0]["subject"] == "Project update"
    assert result.data["messages"][0]["from"] == "alice@example.com"


@pytest.mark.asyncio
async def test_search_returns_message_ids(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get", AsyncMock(return_value=MOCK_MESSAGES_LIST)):
        result = await tool.execute("search", {"query": "subject:meeting"})
    assert result.success
    assert len(result.data["messages"]) == 1


@pytest.mark.asyncio
async def test_no_token_returns_error(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: None)
    result = await tool.execute("read_recent", {"limit": 1})
    assert not result.success


def test_summarize_inbox_schema(tool):
    assert "summarize_inbox" in tool.verbs
    assert tool.verbs["summarize_inbox"].auth_class == "intimate"


def test_all_verbs_are_intimate(tool):
    for verb in tool.verbs.values():
        assert verb.auth_class == "intimate"
