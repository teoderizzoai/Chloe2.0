"""E-06: query_mixed returns the requested kind-quota composition."""
from __future__ import annotations

from pathlib import Path

import pytest

from chloe.state.db import migrate, close
from chloe.state.chroma import reset_client
from chloe.memory.store import add
from chloe.memory.retrieval import query_mixed, DEFAULT_MIX

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

COLLECTION = "test_memories_retrieval"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.fixture(autouse=True)
def fresh_chroma():
    _drop_collection(COLLECTION)
    yield
    _drop_collection(COLLECTION)


def _drop_collection(name: str) -> None:
    from chloe.state.chroma import get_client
    try:
        get_client().delete_collection(name)
    except Exception:
        pass


def _insert_memories(counts: dict[str, int]) -> list[int]:
    ids = []
    topics = {
        "episodic": "remembered a walk in the park",
        "semantic": "Teo dislikes grapefruit",
        "autobiographical": "I have grown quieter this month",
        "procedural": "never add calendar reminders too close to meetings",
    }
    for kind, n in counts.items():
        for i in range(n):
            mid = add(
                kind=kind,
                text=f"{topics[kind]} (sample {i})",
                collection_name=COLLECTION,
            )
            ids.append(mid)
    return ids


def test_query_mixed_returns_all_kinds():
    _insert_memories({"episodic": 15, "semantic": 6, "autobiographical": 3, "procedural": 4})

    results = query_mixed("park walk grapefruit", collection_name=COLLECTION)
    kinds_found = {m.kind for m in results}
    assert "episodic" in kinds_found
    assert "semantic" in kinds_found


def test_query_mixed_respects_quotas():
    _insert_memories({"episodic": 15, "semantic": 6, "autobiographical": 3, "procedural": 4})

    custom_mix = {"episodic": 3, "semantic": 2, "autobiographical": 1, "procedural": 1}
    results = query_mixed("general query", kinds_mix=custom_mix, collection_name=COLLECTION)

    by_kind: dict[str, int] = {}
    for m in results:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1

    for kind, quota in custom_mix.items():
        assert by_kind.get(kind, 0) <= quota, \
            f"kind={kind} returned {by_kind.get(kind, 0)} > quota {quota}"


def test_query_mixed_30_memories_default_mix():
    """Integration: 30 memories (mixed kinds) → results match default quota-mix."""
    _insert_memories({"episodic": 15, "semantic": 7, "autobiographical": 5, "procedural": 3})

    results = query_mixed("something about life and memory", collection_name=COLLECTION)

    by_kind: dict[str, int] = {}
    for m in results:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1

    for kind, quota in DEFAULT_MIX.items():
        assert by_kind.get(kind, 0) <= quota, \
            f"kind={kind} returned {by_kind.get(kind, 0)} > quota {quota}"


def test_empty_collection_returns_empty():
    results = query_mixed("anything", collection_name=COLLECTION)
    assert results == []


def test_empty_query_returns_empty():
    _insert_memories({"episodic": 3})
    results = query_mixed("", collection_name=COLLECTION)
    assert results == []


def test_anchor_bonus_applied():
    from chloe.state.db import get_connection
    import json

    mem_id = add(
        kind="episodic",
        text="I queued a track for Teo",
        source="action",
        artifact_refs=[{"kind": "spotify_track", "ref": "spotify:track:test123",
                        "snapshot": "Test Song"}],
        collection_name=COLLECTION,
    )

    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO artifact_index (kind, ref, title, exists_) VALUES (?, ?, ?, 1)",
        ("spotify_track", "spotify:track:test123", "Test Song"),
    )
    conn.commit()

    # Add an identical memory without an artifact ref to compare scores
    plain_id = add(
        kind="episodic",
        text="I queued a track for Teo",
        source="action",
        collection_name=COLLECTION,
    )

    results = query_mixed("track song music queue", collection_name=COLLECTION)
    anchor_mem = next((m for m in results if m.id == mem_id), None)
    plain_mem = next((m for m in results if m.id == plain_id), None)
    assert anchor_mem is not None
    assert plain_mem is not None
    # Anchor memory should score higher due to the bonus
    assert anchor_mem.score > plain_mem.score
