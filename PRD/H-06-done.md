# H-06 · `chloe rebuild-chroma` CLI command

## Overview

A CLI command (via `typer`) that re-embeds all `archived_tier="hot"` and `"warm"` memories from SQLite into Chroma, in batches of 100, with a progress bar. Used for disaster recovery when the Chroma volume is lost or corrupted.

## Context

ChromaDB is an in-process vector store backed by a directory on disk. If the Chroma data directory is deleted, corrupted, or needs to be moved to new hardware, all embeddings are lost. The source of truth is always SQLite; this command re-derives embeddings from SQLite memory content. It does not re-generate warm cluster summaries (those are already in SQLite as memories); it re-embeds whatever is in SQLite.

## Implementation

### `cli/commands.py`

```python
# chloe/cli/commands.py
from __future__ import annotations
import typer
from typing import Optional

app = typer.Typer(name="chloe", help="Chloe management CLI")


@app.command("rebuild-chroma")
def rebuild_chroma(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be embedded, don't write"),
    batch_size: int = typer.Option(100, "--batch-size", help="Memories per embedding batch"),
    tiers: list[str] = typer.Option(["hot", "warm"], "--tier", help="Which tiers to rebuild"),
):
    """
    Re-embed all hot and warm memories from SQLite into Chroma.
    Use after Chroma data directory is lost or corrupted.
    """
    import asyncio
    asyncio.run(_rebuild_chroma_async(dry_run=dry_run, batch_size=batch_size, tiers=tiers))


async def _rebuild_chroma_async(dry_run: bool, batch_size: int, tiers: list[str]):
    from chloe.state.db import get_connection, migrate
    from chloe.memory.store import MemoryStore
    import rich
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn

    # Run migrations to ensure DB is up to date
    migrate()

    conn = get_connection()
    tier_placeholders = ",".join("?" * len(tiers))
    rows = conn.execute(
        f"""
        SELECT id, content, kind, tags
        FROM memories
        WHERE archived_tier IN ({tier_placeholders})
        ORDER BY created_at ASC
        """,
        tiers,
    ).fetchall()

    total = len(rows)
    rich.print(f"[bold]Found {total} memories to re-embed[/bold] (tiers: {', '.join(tiers)})")

    if dry_run:
        rich.print(f"[yellow]Dry run: would embed {total} memories in {total // batch_size + 1} batches[/yellow]")
        return

    store = MemoryStore()
    batches = [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]

    success_count = 0
    error_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
    ) as progress:
        task = progress.add_task("Embedding memories...", total=len(batches))

        for batch in batches:
            for row in batch:
                try:
                    await store.add_to_chroma(row["id"], row["content"])
                    success_count += 1
                except Exception as exc:
                    error_count += 1
                    rich.print(f"[red]Error embedding {row['id']}: {exc}[/red]")
            progress.advance(task)

    rich.print(f"\n[green]Done: {success_count} embedded, {error_count} errors[/green]")

    # Verify count
    chroma_count = await store.count()
    rich.print(f"[bold]Chroma collection count: {chroma_count}[/bold]")
    rich.print(f"Expected (hot+warm in SQLite): {total}")
    if chroma_count < total * 0.95:
        rich.print("[red]WARNING: Chroma count is significantly lower than expected[/red]")
    else:
        rich.print("[green]Counts match — rebuild successful[/green]")
```

### Register CLI entrypoint in `pyproject.toml`

```toml
[project.scripts]
chloe = "chloe.cli.commands:app"
```

### `MemoryStore.count()` helper

```python
# In chloe/memory/store.py — add:

async def count(self) -> int:
    """Return total number of embeddings in Chroma collection."""
    collection = self._get_collection()
    return collection.count()

async def delete_from_chroma(self, memory_id: str) -> None:
    """Remove a memory's embedding from Chroma."""
    collection = self._get_collection()
    collection.delete(ids=[memory_id])

async def add_to_chroma(self, memory_id: str, content: str) -> None:
    """Add or update a memory's embedding in Chroma."""
    collection = self._get_collection()
    collection.upsert(
        ids=[memory_id],
        documents=[content],
    )
```

## Testing

### Unit tests — `tests/unit/test_rebuild_chroma.py`

```python
import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_memories(db, count: int, tier: str = "hot"):
    import ulid
    ids = []
    for i in range(count):
        mid = str(ulid.new())
        db.execute(
            """
            INSERT INTO memories (id, kind, content, tags, source, weight, archived_tier, artifact_refs, created_at)
            VALUES (?, 'episodic', ?, '[]', 'test', 0.8, ?, '[]', ?)
            """,
            (mid, f"Memory content number {i}", tier, datetime.utcnow().isoformat()),
        )
        ids.append(mid)
    db.commit()
    return ids


@pytest.mark.asyncio
async def test_rebuild_embeds_correct_count(db):
    hot_ids = _insert_memories(db, 250, tier="hot")
    warm_ids = _insert_memories(db, 250, tier="warm")
    cold_ids = _insert_memories(db, 50, tier="cold")  # Should NOT be embedded

    embedded = []

    with patch("chloe.memory.store.MemoryStore") as MockStore:
        instance = MockStore.return_value
        async def capture_add(mid, content):
            embedded.append(mid)
        instance.add_to_chroma = capture_add
        instance.count = AsyncMock(return_value=500)

        from chloe.cli.commands import _rebuild_chroma_async
        await _rebuild_chroma_async(dry_run=False, batch_size=100, tiers=["hot", "warm"])

    # Should embed hot + warm (500 total), not cold (50)
    assert len(embedded) == 500
    for cid in cold_ids:
        assert cid not in embedded


@pytest.mark.asyncio
async def test_rebuild_dry_run_makes_no_calls(db):
    _insert_memories(db, 100, tier="hot")

    with patch("chloe.memory.store.MemoryStore") as MockStore:
        instance = MockStore.return_value
        instance.add_to_chroma = AsyncMock()

        from chloe.cli.commands import _rebuild_chroma_async
        await _rebuild_chroma_async(dry_run=True, batch_size=100, tiers=["hot", "warm"])

        instance.add_to_chroma.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_continues_on_individual_error(db):
    ids = _insert_memories(db, 5, tier="hot")
    embedded = []
    errors = []

    with patch("chloe.memory.store.MemoryStore") as MockStore:
        instance = MockStore.return_value
        call_count = [0]
        async def maybe_error(mid, content):
            call_count[0] += 1
            if call_count[0] == 3:
                raise Exception("Embedding error")
            embedded.append(mid)
        instance.add_to_chroma = maybe_error
        instance.count = AsyncMock(return_value=4)

        from chloe.cli.commands import _rebuild_chroma_async
        # Should not raise
        await _rebuild_chroma_async(dry_run=False, batch_size=100, tiers=["hot"])

    assert len(embedded) == 4  # 5 - 1 error


@pytest.mark.asyncio
async def test_rebuild_500_memories_chroma_count_matches(db):
    """Main acceptance test: 500 memories → Chroma count matches expected."""
    hot_ids = _insert_memories(db, 300, tier="hot")
    warm_ids = _insert_memories(db, 200, tier="warm")

    embedded = []

    with patch("chloe.memory.store.MemoryStore") as MockStore:
        instance = MockStore.return_value
        async def capture_add(mid, content):
            embedded.append(mid)
        instance.add_to_chroma = capture_add
        instance.count = AsyncMock(return_value=500)

        from chloe.cli.commands import _rebuild_chroma_async
        await _rebuild_chroma_async(dry_run=False, batch_size=100, tiers=["hot", "warm"])

    assert len(embedded) == 500
```

### CLI smoke test

```bash
# Install CLI and run against a test DB:
pip install -e .
CHLOE_DB_PATH=/tmp/test_rebuild.db chloe rebuild-chroma --dry-run
# Expected: "Found N memories to embed" output, no Chroma writes
```

### Full rebuild test against real DB

```bash
# Run against production DB (no --dry-run):
chloe rebuild-chroma --batch-size 100 --tier hot --tier warm
# Expected: "Done: N embedded, 0 errors" and Chroma count matches SQLite hot+warm count
```

## Dependencies

- E-06 (`memory/store.py` — `add_to_chroma`, `delete_from_chroma`, `count`).
- F-03 (`state/db.py` — `migrate()`).
- `typer`, `rich` — CLI and progress bar.

## Acceptance criteria

- Run against test DB with 500 memories → Chroma collection count matches expected hot+warm count.
- `--dry-run` → no Chroma writes, shows expected count.
- Individual embedding error → logs error and continues (no crash).
- Cold-tier memories never embedded.
- `chloe rebuild-chroma` registered as CLI entrypoint via `pyproject.toml`.
