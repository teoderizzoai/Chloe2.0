import httpx
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient

from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set
from chloe.actions import gate, confirm as confirm_mod
from chloe.actions.schema import Action
from chloe.tools.registry import ToolRegistry
from chloe.tools.gmail import GmailTool

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def no_leash():
    with patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")):
        yield


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register(GmailTool())
    return r


@pytest.mark.asyncio
async def test_phase_c_full_confirmation_flow(fresh_db, registry, monkeypatch):
    from chloe.app import create_app
    app = create_app()

    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: registry)
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})

    kv_set("devices", [{"token": "ios_device_tok", "platform": "ios"}])

    push_received = []

    async def mock_push(device_info, payload):
        push_received.append(payload)
        return True

    # --- Step 1: draft_reply (kinetic) executes immediately ---
    async def mock_post_draft(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "draft_001"}
        return resp

    with patch("httpx.AsyncClient") as mock_client, \
         patch("chloe.channels.push.preferred_push", new=mock_push):
        mock_client.return_value.__aenter__.return_value.post = mock_post_draft

        draft_action = Action(
            tool="gmail", verb="draft_reply",
            args={"threadId": "thread_xyz", "body": "Thanks for reaching out!"},
            intent="Draft reply to Alice's email",
            preview="Draft: Thanks for reaching out!",
            authorization="kinetic",
        )
        result_1 = await gate.submit(draft_action)

    assert result_1.executed, f"draft_reply should execute immediately: {result_1}"
    assert not result_1.awaiting

    art = fresh_db.execute("SELECT * FROM artifact_index WHERE kind='gmail_thread'").fetchone()
    assert art is not None

    mem = fresh_db.execute("SELECT * FROM memories WHERE source='action'").fetchone()
    assert mem is not None
    assert "Alice" in mem["text"]

    # Allow kinetic-sensitive actions through the leash
    fresh_db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES ('auth_ceiling', ?)",
        (json.dumps("kinetic-sensitive"),),
    )
    fresh_db.commit()

    # --- Step 2: send_reply (kinetic-sensitive) → awaiting confirmation ---
    async def mock_post_send(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "sent_msg_001"}
        return resp

    with patch("httpx.AsyncClient") as mock_client, \
         patch("chloe.channels.push.preferred_push", new=mock_push):
        mock_client.return_value.__aenter__.return_value.post = mock_post_send

        send_action = Action(
            tool="gmail", verb="send_reply",
            args={"draftId": "draft_001"},
            intent="Send reply to Alice via draft",
            preview="Send: Thanks for reaching out!",
            authorization="kinetic-sensitive",
        )
        result_2 = await gate.submit(send_action)

    assert result_2.awaiting, f"send_reply should await confirmation: {result_2}"
    assert result_2.ticket_id
    assert not result_2.executed

    assert len(push_received) >= 1
    conf_push = next((p for p in push_received if p.get("type") == "confirmation"), None)
    assert conf_push is not None, f"Expected confirmation push, got: {push_received}"
    assert conf_push["ticket_id"] == result_2.ticket_id
    assert conf_push["action_tool"] == "gmail"

    row = fresh_db.execute(
        "SELECT state, ticket_id FROM actions WHERE id=?", (send_action.id,)
    ).fetchone()
    assert row["state"] == "awaiting_confirmation"

    # --- Step 3: POST /v1/confirmations/{id}/confirm ---
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/v1/confirmations/{result_2.ticket_id}/confirm")

    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"

    # --- Step 4: Verify final state ---
    action_row = fresh_db.execute("SELECT state FROM actions WHERE id=?", (send_action.id,)).fetchone()
    assert action_row["state"] == "confirmed"
