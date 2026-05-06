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
        lambda: MagicMock(ha_base_url="http://ha.test:8123", ha_token="test_token"),
    )
    from chloe.tools.smart_home import SmartHomeTool
    return SmartHomeTool()


def test_verb_auth_classes(tool):
    assert tool.verbs["lights"].auth_class == "kinetic-sensitive"
    assert tool.verbs["thermostat"].auth_class == "kinetic-sensitive"
    assert tool.verbs["scene"].auth_class == "kinetic-sensitive"
    assert tool.verbs["media_player"].auth_class == "kinetic"


def test_dry_run_lights(tool):
    result = tool.dry_run("lights", {"entity": "light.bedroom", "state": "off"})
    assert "light.bedroom" in result and "off" in result


def test_dry_run_thermostat(tool):
    result = tool.dry_run("thermostat", {"entity": "climate.main", "temperature": 21.5})
    assert "21.5" in result


def test_dry_run_scene(tool):
    result = tool.dry_run("scene", {"name": "scene.movie_night"})
    assert "movie_night" in result


@pytest.mark.asyncio
async def test_lights_success(tool, db):
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("lights", {"entity": "light.living_room", "state": "on"})
    assert result.success
    assert result.data["service"] == "turn_on"


@pytest.mark.asyncio
async def test_lights_off(tool, db):
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("lights", {"entity": "light.living_room", "state": "off"})
    assert result.success
    assert result.data["service"] == "turn_off"


@pytest.mark.asyncio
async def test_blocklist_blocks_entity(tool, db):
    db.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(["lock.front_door"])),
    )
    db.commit()
    result = await tool.execute("lights", {"entity": "lock.front_door", "state": "off"})
    assert not result.success
    assert "blocklist" in result.error


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
async def test_ha_connect_error(tool, db):
    import httpx
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )
        result = await tool.execute("lights", {"entity": "light.office", "state": "on"})
    assert not result.success
    assert "HomeAssistant" in result.error


@pytest.mark.asyncio
async def test_media_player_play(tool, db):
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("media_player", {"entity": "media_player.bedroom_tv", "op": "play"})
    assert result.success
    assert result.data["service"] == "media_play"


@pytest.mark.asyncio
async def test_scene_activate(tool, db):
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=200, json=lambda: [], text="")
        )
        result = await tool.execute("scene", {"name": "scene.movie_night"})
    assert result.success
    assert result.data["service"] == "turn_on"


@pytest.mark.asyncio
async def test_media_player_unknown_op(tool, db):
    result = await tool.execute("media_player", {"entity": "media_player.tv", "op": "rewind"})
    assert not result.success
    assert "rewind" in result.error


@pytest.mark.asyncio
async def test_ha_api_error(tool, db):
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=MagicMock(status_code=400, text="Bad request")
        )
        result = await tool.execute("lights", {"entity": "light.living_room", "state": "on"})
    assert not result.success
    assert "400" in result.error
