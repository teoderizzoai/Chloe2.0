import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from chloe.channels.push_apns import _build_payload, send_push


def test_message_payload_shape():
    payload = _build_payload({"type": "message", "title": "Hello", "body": "World"})
    assert payload["aps"]["alert"]["title"] == "Hello"
    assert payload["aps"]["alert"]["body"] == "World"
    assert payload["aps"]["sound"] == "default"


def test_confirmation_payload_shape():
    payload = _build_payload({
        "type": "confirmation",
        "preview": "Queue track on Spotify?",
        "ticket_id": "ticket_abc",
        "action_tool": "spotify",
        "action_verb": "queue_track",
    })
    assert payload["aps"]["category"] == "CONFIRMATION"
    assert payload["ticket_id"] == "ticket_abc"
    assert payload["action_tool"] == "spotify"
    assert "Queue track" in payload["aps"]["alert"]["body"]


@pytest.mark.asyncio
async def test_send_push_success(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_path = tmp_path / "apns.p8"
    key_path.write_bytes(pem)

    from chloe.config import Settings
    monkeypatch.setattr(
        "chloe.channels.push_apns.get_settings",
        lambda: Settings(
            apns_key_id="ABCDE12345",
            apns_team_id="TEAM123456",
            apns_key_path=str(key_path),
            apns_bundle_id="com.teo.chloe",
            apns_environment="sandbox",
        ),
    )
    # Reset cache
    from chloe.channels import push_apns
    push_apns._token_cache.clear()

    with patch("httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        result = await send_push("device_token_abc123", {"type": "message", "body": "Hi"})

    assert result is True


@pytest.mark.asyncio
async def test_send_push_410_removes_token(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_path = tmp_path / "apns.p8"
    key_path.write_bytes(pem)

    removed = []
    from chloe.channels import push_apns
    push_apns._token_cache.clear()
    monkeypatch.setattr(push_apns, "_remove_device_token", lambda t: removed.append(t))
    monkeypatch.setattr(
        push_apns,
        "get_settings",
        lambda: MagicMock(
            apns_key_id="K",
            apns_team_id="T",
            apns_key_path=str(key_path),
            apns_bundle_id="b",
            apns_environment="sandbox",
        ),
    )

    with patch("httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
        result = await send_push("bad_token", {"type": "message", "body": "test"})

    assert result is False
    assert "bad_token" in removed


@pytest.mark.asyncio
async def test_send_push_missing_key(monkeypatch):
    from chloe.channels import push_apns
    push_apns._token_cache.clear()
    monkeypatch.setattr(
        push_apns,
        "get_settings",
        lambda: MagicMock(apns_key_path="/nonexistent/key.p8", apns_environment="sandbox"),
    )
    result = await send_push("token", {"type": "message", "body": "test"})
    assert result is False
