import pytest
from unittest.mock import AsyncMock, patch

from chloe.channels.push import preferred_push


@pytest.mark.asyncio
async def test_ios_dispatches_to_apns(monkeypatch):
    mock_apns = AsyncMock(return_value=True)
    monkeypatch.setattr("chloe.channels.push_apns.send_push", mock_apns)

    result = await preferred_push(
        {"platform": "ios", "token": "ios_tok"},
        {"type": "message", "body": "hi"},
    )
    assert result is True
    mock_apns.assert_called_once_with("ios_tok", {"type": "message", "body": "hi"})


@pytest.mark.asyncio
async def test_android_dispatches_to_fcm(monkeypatch):
    mock_fcm = AsyncMock(return_value=True)
    monkeypatch.setattr("chloe.channels.push_fcm.send_push", mock_fcm)

    result = await preferred_push(
        {"platform": "android", "token": "android_tok"},
        {"type": "message", "body": "hi"},
    )
    assert result is True
    mock_fcm.assert_called_once_with("android_tok", {"type": "message", "body": "hi"})


@pytest.mark.asyncio
async def test_empty_token_returns_false():
    result = await preferred_push({"platform": "ios", "token": ""}, {"type": "message"})
    assert result is False
