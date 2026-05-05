from pathlib import Path

import pytest

from chloe.actions import audit
from chloe.actions.schema import Action
from chloe.state.db import close, migrate

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.mark.asyncio
async def test_build_dynamic_suffix_returns_string():
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix(person_id="teo")
    assert isinstance(suffix, str)
    assert len(suffix) > 0


@pytest.mark.asyncio
async def test_audit_context_included_after_action():
    a = Action(
        tool="notes", verb="append",
        intent="added a thought about the ocean",
        preview="Append to notes",
        authorization="kinetic",
        state="executed",
    )
    await audit.append(a)

    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix(person_id="teo")
    assert "notes" in suffix
    assert "append" in suffix or "ocean" in suffix


@pytest.mark.asyncio
async def test_suffix_graceful_when_no_actions():
    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = await build_dynamic_suffix("teo")
    assert "recent" in suffix.lower() or "action" in suffix.lower()
