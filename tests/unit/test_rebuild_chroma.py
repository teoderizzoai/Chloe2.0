import pytest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    close()  # Reset any connection left open by a previous test
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_memories(db, count: int, tier: str = "hot"):
    ids = []
    now = datetime.now(timezone.utc).isoformat()
    for i in range(count):
        db.execute(
            """
            INSERT INTO memories (kind, text, source, tags, artifact_refs, weight,
                                  salience, confidence, archived_tier, created_at, updated_at)
            VALUES ('episodic', ?, 'test', '[]', '[]', 0.8, 0.5, 1.0, ?, ?, ?)
            """,
            (f"Memory content number {i}", tier, now, now),
        )
        ids.append(db.execute("SELECT last_insert_rowid() as id").fetchone()["id"])
    db.commit()
    return ids


@pytest.mark.asyncio
async def test_rebuild_embeds_correct_count(db):
    _insert_memories(db, 250, tier="hot")
    _insert_memories(db, 250, tier="warm")
    cold_ids = _insert_memories(db, 50, tier="cold")

    embedded = []

    with patch("chloe.memory.store.chroma_add") as mock_add:
        mock_add.side_effect = lambda mid, text, kind, **kw: embedded.append(mid)
        with patch("chloe.memory.store.chroma_count", return_value=500):
            from chloe.cli.commands import _rebuild_chroma_async
            await _rebuild_chroma_async(dry_run=False, batch_size=100, tiers=["hot", "warm"])

    assert len(embedded) == 500
    for cid in cold_ids:
        assert cid not in embedded


@pytest.mark.asyncio
async def test_rebuild_dry_run_makes_no_calls(db):
    _insert_memories(db, 100, tier="hot")

    with patch("chloe.memory.store.chroma_add") as mock_add:
        from chloe.cli.commands import _rebuild_chroma_async
        await _rebuild_chroma_async(dry_run=True, batch_size=100, tiers=["hot", "warm"])
        mock_add.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_continues_on_individual_error(db):
    _insert_memories(db, 5, tier="hot")
    embedded = []
    call_count = [0]

    def maybe_error(mid, text, kind, **kw):
        call_count[0] += 1
        if call_count[0] == 3:
            raise Exception("Embedding error")
        embedded.append(mid)

    with patch("chloe.memory.store.chroma_add", side_effect=maybe_error):
        with patch("chloe.memory.store.chroma_count", return_value=4):
            from chloe.cli.commands import _rebuild_chroma_async
            await _rebuild_chroma_async(dry_run=False, batch_size=100, tiers=["hot"])

    assert len(embedded) == 4


@pytest.mark.asyncio
async def test_rebuild_500_memories_chroma_count_matches(db):
    _insert_memories(db, 300, tier="hot")
    _insert_memories(db, 200, tier="warm")

    embedded = []

    with patch("chloe.memory.store.chroma_add") as mock_add:
        mock_add.side_effect = lambda mid, text, kind, **kw: embedded.append(mid)
        with patch("chloe.memory.store.chroma_count", return_value=500):
            from chloe.cli.commands import _rebuild_chroma_async
            await _rebuild_chroma_async(dry_run=False, batch_size=100, tiers=["hot", "warm"])

    assert len(embedded) == 500
