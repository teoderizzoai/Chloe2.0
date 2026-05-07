import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.memory.retention import run_retention_job

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_memory(db, tier="hot", days_old=0, kind="episodic"):
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    db.execute(
        """
        INSERT INTO memories (kind, text, source, tags, artifact_refs, weight, salience, confidence,
                              archived_tier, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (kind, f"Memory content created at {created_at}", "test", "[]", "[]",
         0.8, 0.5, 1.0, tier, created_at, created_at),
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]


@pytest.mark.asyncio
async def test_hot_to_warm_promotes_old_memories(db):
    ids = [_insert_memory(db, tier="hot", days_old=100) for _ in range(11)]

    mock_summary = {"summary": "Cluster summary of related memories."}

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=mock_summary)
        with patch("chloe.memory.store.add_to_chroma"):
            with patch("chloe.memory.store.delete_from_chroma"):
                stats = await run_retention_job()

    assert stats["warm_promoted"] == 11
    assert stats["clusters_created"] >= 1

    conn = get_connection()
    placeholders = ",".join("?" * len(ids))
    warm_count = conn.execute(
        f"SELECT COUNT(*) as n FROM memories WHERE archived_tier='warm' AND id IN ({placeholders})",
        ids
    ).fetchone()["n"]
    assert warm_count == 11


@pytest.mark.asyncio
async def test_recent_memories_not_promoted(db):
    [_insert_memory(db, tier="hot", days_old=30) for _ in range(5)]

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value={"summary": "x"})
        with patch("chloe.memory.store.add_to_chroma"):
            with patch("chloe.memory.store.delete_from_chroma"):
                stats = await run_retention_job()

    assert stats["warm_promoted"] == 0


@pytest.mark.asyncio
async def test_warm_to_cold_removes_from_chroma(db):
    ids = [_insert_memory(db, tier="warm", days_old=800) for _ in range(3)]

    removed_ids = []

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock()
        with patch("chloe.memory.store.add_to_chroma"):
            with patch("chloe.memory.store.delete_from_chroma") as mock_del:
                def capture_delete(mid, **kwargs):
                    removed_ids.append(mid)
                mock_del.side_effect = capture_delete
                stats = await run_retention_job()

    assert stats["cold_promoted"] == 3

    conn = get_connection()
    placeholders = ",".join("?" * len(ids))
    cold = conn.execute(
        f"SELECT COUNT(*) as n FROM memories WHERE archived_tier='cold' AND id IN ({placeholders})",
        ids
    ).fetchone()["n"]
    assert cold == 3


@pytest.mark.asyncio
async def test_dry_run_makes_no_changes(db):
    [_insert_memory(db, tier="hot", days_old=100) for _ in range(5)]
    conn = get_connection()
    hot_before = conn.execute("SELECT COUNT(*) as n FROM memories WHERE archived_tier='hot'").fetchone()["n"]

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value={"summary": "summary"})
        with patch("chloe.memory.store.add_to_chroma"):
            with patch("chloe.memory.store.delete_from_chroma"):
                await run_retention_job(dry_run=True)

    hot_after = conn.execute("SELECT COUNT(*) as n FROM memories WHERE archived_tier='hot'").fetchone()["n"]
    assert hot_before == hot_after


@pytest.mark.asyncio
async def test_cluster_summary_written_to_sqlite(db):
    [_insert_memory(db, tier="hot", days_old=100) for _ in range(11)]

    with patch("chloe.memory.retention._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value={"summary": "Cluster summary."})
        with patch("chloe.memory.store.add_to_chroma"):
            with patch("chloe.memory.store.delete_from_chroma"):
                await run_retention_job()

    conn = get_connection()
    old_hot = conn.execute(
        "SELECT COUNT(*) as n FROM memories WHERE archived_tier='hot'"
    ).fetchone()["n"]
    assert old_hot == 0
