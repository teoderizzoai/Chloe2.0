import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from chloe.channels.push_fcm import _build_message, send_push


def test_message_payload_structure():
    msg = _build_message("tok123", {"type": "message", "title": "Hi", "body": "World"})
    assert msg["message"]["token"] == "tok123"
    assert msg["message"]["notification"]["title"] == "Hi"
    assert msg["message"]["android"]["priority"] == "high"


def test_confirmation_payload_structure():
    msg = _build_message("tok123", {
        "type": "confirmation",
        "preview": "Add calendar event?",
        "ticket_id": "tkt1",
        "action_tool": "calendar",
        "action_verb": "add_event",
    })
    assert msg["message"]["data"]["ticket_id"] == "tkt1"
    assert msg["message"]["data"]["type"] == "confirmation"
    assert "Add calendar" in msg["message"]["notification"]["body"]


@pytest.mark.asyncio
async def test_send_push_success(monkeypatch):
    monkeypatch.setattr(
        "chloe.channels.push_fcm._get_access_token", AsyncMock(return_value="access_tok")
    )
    monkeypatch.setattr(
        "chloe.channels.push_fcm.get_settings", lambda: MagicMock(fcm_project_id="proj1")
    )

    with patch("httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        result = await send_push("dev_token", {"type": "message", "body": "test"})

    assert result is True


@pytest.mark.asyncio
async def test_send_push_no_token(monkeypatch):
    monkeypatch.setattr(
        "chloe.channels.push_fcm._get_access_token", AsyncMock(return_value=None)
    )
    result = await send_push("dev_token", {"type": "message", "body": "test"})
    assert result is False
