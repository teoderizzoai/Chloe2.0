import base64
import email
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from chloe.state.db import migrate, close, get_connection
from chloe.tools.gmail import GmailTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture
def tool():
    return GmailTool()


@pytest.fixture
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_dry_run_draft(tool):
    preview = tool.dry_run("draft_reply", {"threadId": "thread123", "body": "Hello there"})
    assert "thread123" in preview
    assert "Hello" in preview


def test_draft_reply_verb_is_kinetic(tool):
    assert tool.verbs["draft_reply"].auth_class == "kinetic"


@pytest.mark.asyncio
async def test_no_token_returns_error(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: None)
    result = await tool.execute("draft_reply", {"threadId": "t1", "body": "Test"})
    assert not result.success
    assert "token" in result.error.lower()


@pytest.mark.asyncio
async def test_footer_appended_by_default(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    captured_body = {}

    async def mock_post(url, headers=None, json=None):
        captured_body.update(json or {})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_abc"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("draft_reply", {"threadId": "t1", "body": "Hello Teo"})

    assert result.success
    assert result.data["draftId"] == "draft_abc"
    raw_b64 = captured_body["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
    msg = email.message_from_bytes(raw_bytes)
    body_text = msg.get_payload(decode=True).decode("utf-8")
    assert "sent with help from Chloe" in body_text


@pytest.mark.asyncio
async def test_footer_suppressed_when_preference_false(tool, db, monkeypatch):
    db.execute("INSERT OR REPLACE INTO preferences (key, value) VALUES ('gmail_footer', ?)", (json.dumps(False),))
    db.commit()
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    captured_body = {}

    async def mock_post(url, headers=None, json=None):
        captured_body.update(json or {})
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_xyz"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("draft_reply", {"threadId": "t2", "body": "No footer"})

    assert result.success
    raw_b64 = captured_body["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
    msg = email.message_from_bytes(raw_bytes)
    body_text = msg.get_payload(decode=True).decode("utf-8")
    assert "Chloe" not in body_text


@pytest.mark.asyncio
async def test_artifact_row_written(tool, db, monkeypatch):
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    async def mock_post(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_r1"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        await tool.execute("draft_reply", {"threadId": "thread_abc", "body": "Hi"})

    row = db.execute("SELECT * FROM artifact_index WHERE ref='thread_abc'").fetchone()
    assert row is not None
    assert row["kind"] == "gmail_thread"
