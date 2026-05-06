import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chloe.state.db import close, get_connection, migrate

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr(
        "chloe.tools.smart_home.get_settings",
        lambda: MagicMock(ha_base_url="http://ha.test:8123", ha_token="tok"),
    )
    from chloe.tools.smart_home import SmartHomeTool
    return SmartHomeTool()


@pytest.mark.asyncio
async def test_allowlist_blocks_unlisted_entity(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.living_room"])),
    )
    db.commit()
    result = await tool.execute("lights", {"entity": "light.bedroom", "state": "on"})
    assert not result.success
    assert "allowlist" in result.error


@pytest.mark.asyncio
async def test_allowlist_permits_listed_entity(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.bedroom"])),
    )
    db.commit()
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("lights", {"entity": "light.bedroom", "state": "on"})
    assert result.success


@pytest.mark.asyncio
async def test_blocklist_blocks_even_if_on_allowlist(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["lock.front_door"])),
    )
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(["lock.front_door"])),
    )
    db.commit()
    result = await tool.execute("lights", {"entity": "lock.front_door", "state": "off"})
    assert not result.success
    assert "blocklist" in result.error


@pytest.mark.asyncio
async def test_no_allowlist_permits_all(tool, db):
    db.execute("DELETE FROM preferences WHERE key='ha_allowlist'")
    db.commit()
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("lights", {"entity": "light.any_entity", "state": "on"})
    assert result.success


@pytest.mark.asyncio
async def test_gate_denies_unlisted_entity(db):
    from chloe.actions.gate import _check_ha_allowlist
    from chloe.actions.schema import Action

    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.living_room"])),
    )
    db.commit()

    action = Action(
        tool="smart_home",
        verb="lights",
        args={"entity": "light.unlisted", "state": "on"},
        intent="Turn on unlisted light",
        preview="lights on",
        authorization="kinetic-sensitive",
    )
    denial = await _check_ha_allowlist(action)
    assert denial is not None
    assert "allowlist" in denial


@pytest.mark.asyncio
async def test_gate_permits_listed_entity(db):
    from chloe.actions.gate import _check_ha_allowlist
    from chloe.actions.schema import Action

    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(["light.living_room"])),
    )
    db.commit()

    action = Action(
        tool="smart_home",
        verb="lights",
        args={"entity": "light.living_room", "state": "on"},
        intent="Turn on living room light",
        preview="lights on",
        authorization="kinetic-sensitive",
    )
    denial = await _check_ha_allowlist(action)
    assert denial is None


@pytest.mark.asyncio
async def test_gate_no_allowlist_permits_all(db):
    from chloe.actions.gate import _check_ha_allowlist
    from chloe.actions.schema import Action

    db.execute("DELETE FROM preferences WHERE key='ha_allowlist'")
    db.commit()

    action = Action(
        tool="smart_home",
        verb="lights",
        args={"entity": "light.any", "state": "on"},
        intent="Turn on light",
        preview="lights on",
        authorization="kinetic-sensitive",
    )
    denial = await _check_ha_allowlist(action)
    assert denial is None


@pytest.mark.asyncio
async def test_blocklist_check_fires_before_http(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(["switch.smoke_detector"])),
    )
    db.commit()

    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock()
        result = await tool.execute("lights", {"entity": "switch.smoke_detector", "state": "off"})
        mock.return_value.__aenter__.return_value.post.assert_not_called()

    assert not result.success


@pytest.mark.asyncio
async def test_empty_allowlist_key_permits_all(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps([])),
    )
    db.commit()
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("lights", {"entity": "light.anything", "state": "on"})
    assert result.success
