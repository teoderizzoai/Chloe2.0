import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.actions.schema import Action
from chloe.llm.schemas import Verdict

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.fixture(autouse=True)
def no_leash():
    with patch("chloe.actions.gate.leash_mod.violates", return_value=(False, "")):
        yield


def _make_action(**kwargs):
    defaults = dict(
        tool="spotify", verb="queue_track",
        args={"uri": "spotify:track:x"},
        intent="Queue a track",
        preview="Queue track",
        authorization="kinetic",
    )
    defaults.update(kwargs)
    return Action(**defaults)


@pytest.mark.asyncio
async def test_deliberate_proceed(monkeypatch):
    mock_flash = AsyncMock(return_value={"decision": "proceed", "reason": "Seems fine"})
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))

    from chloe.actions.deliberate import deliberate
    action = _make_action()
    verdict = await deliberate(action)

    assert verdict is not None
    assert verdict.decision == "proceed"


@pytest.mark.asyncio
async def test_deliberate_abort(monkeypatch):
    mock_flash = AsyncMock(return_value={"decision": "abort", "reason": "Too many recent actions"})
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))

    from chloe.actions.deliberate import deliberate
    action = _make_action()
    verdict = await deliberate(action)

    assert verdict.decision == "abort"
    assert "recent" in verdict.reason


@pytest.mark.asyncio
async def test_deliberate_llm_failure_treats_as_proceed(monkeypatch):
    mock_flash = AsyncMock(return_value=None)
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))

    from chloe.actions.deliberate import deliberate
    action = _make_action()
    verdict = await deliberate(action)

    assert verdict is None


@pytest.mark.asyncio
async def test_gate_aborts_on_deliberation(monkeypatch):
    from chloe.actions import gate

    mock_flash = AsyncMock(return_value={"decision": "abort", "reason": "Too chatty today"})
    monkeypatch.setattr("chloe.actions.deliberate.get_llm", lambda: MagicMock(flash=mock_flash))
    monkeypatch.setattr("chloe.actions.deliberate.should_deliberate", lambda a: True)

    mock_registry = MagicMock()
    mock_registry.execute = AsyncMock()
    monkeypatch.setattr("chloe.actions.gate.get_registry", lambda: mock_registry)

    action = _make_action()
    result = await gate.submit(action)

    assert result.suppressed
    mock_registry.execute.assert_not_called()

    row = get_connection().execute("SELECT state FROM actions WHERE id=?", (action.id,)).fetchone()
    assert row["state"] == "held_back"
