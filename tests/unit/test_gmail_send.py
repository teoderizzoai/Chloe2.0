import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chloe.state.db import close, get_connection, migrate
from chloe.tools.gmail import GmailTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def tool():
    return GmailTool()


def test_send_reply_verb_is_kinetic_sensitive(tool):
    assert tool.verbs["send_reply"].auth_class == "kinetic-sensitive"


def test_send_reply_reversibility_zero(tool):
    assert tool.verbs["send_reply"].reversibility == 0.0


def test_dry_run_send_reply(tool):
    preview = tool.dry_run("send_reply", {"draftId": "draft_abc"})
    assert "draft_abc" in preview
    assert "cannot be undone" in preview.lower()


@pytest.mark.asyncio
async def test_send_reply_success(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    async def mock_get(url, headers=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"payload": {"headers": [{"name": "To", "value": "alice@example.com"}]}}
        }
        return resp

    async def mock_post(url, headers=None, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "msg_sent_001"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("send_reply", {"draftId": "draft_123"})

    assert result.success
    assert result.data["messageId"] == "msg_sent_001"


@pytest.mark.asyncio
async def test_send_reply_blocked_by_dont_send_to(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("gmail_dont_send_to", json.dumps(["boss@company.com"])),
    )
    db.commit()

    async def mock_get(url, headers=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"payload": {"headers": [{"name": "To", "value": "boss@company.com"}]}}
        }
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        mock_client.return_value.__aenter__.return_value.post = AsyncMock()
        result = await tool.execute("send_reply", {"draftId": "draft_blocked"})

    assert not result.success
    assert "blocked" in result.error.lower()
    mock_client.return_value.__aenter__.return_value.post.assert_not_called()


@pytest.mark.asyncio
async def test_send_reply_no_token(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: None)
    result = await tool.execute("send_reply", {"draftId": "draft_x"})
    assert not result.success
    assert "token" in result.error.lower()


@pytest.mark.asyncio
async def test_send_reply_missing_draft_id(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    result = await tool.execute("send_reply", {})
    assert not result.success
    assert "draftId" in result.error


@pytest.mark.asyncio
async def test_send_reply_api_error(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    async def mock_get(url, headers=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "message": {"payload": {"headers": [{"name": "To", "value": "ok@example.com"}]}}
        }
        return resp

    async def mock_post(url, headers=None, json=None, **kwargs):
        resp = MagicMock()
        resp.status_code = 500
        resp.json.return_value = {}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("send_reply", {"draftId": "draft_err"})

    assert not result.success
    assert "500" in result.error
