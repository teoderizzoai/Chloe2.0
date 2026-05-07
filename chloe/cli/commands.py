from __future__ import annotations

import asyncio

import typer

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
    asyncio.run(_rebuild_chroma_async(dry_run=dry_run, batch_size=batch_size, tiers=tiers))


async def _rebuild_chroma_async(dry_run: bool, batch_size: int, tiers: list[str]) -> None:
    from chloe.state.db import migrate
    from chloe.state.db import get_connection
    import chloe.memory.store as _store
    import rich
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn

    migrate()

    conn = get_connection()
    tier_placeholders = ",".join("?" * len(tiers))
    rows = conn.execute(
        f"""
        SELECT id, text, kind, tags
        FROM memories
        WHERE archived_tier IN ({tier_placeholders})
        ORDER BY created_at ASC
        """,
        tiers,
    ).fetchall()

    total = len(rows)
    rich.print(f"[bold]Found {total} memories to re-embed[/bold] (tiers: {', '.join(tiers)})")

    if dry_run:
        batches_needed = (total // batch_size) + (1 if total % batch_size else 0)
        rich.print(f"[yellow]Dry run: would embed {total} memories in {batches_needed} batches[/yellow]")
        return

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
                    _store.chroma_add(row["id"], row["text"], kind=row["kind"])
                    success_count += 1
                except Exception as exc:
                    error_count += 1
                    rich.print(f"[red]Error embedding {row['id']}: {exc}[/red]")
            progress.advance(task)

    rich.print(f"\n[green]Done: {success_count} embedded, {error_count} errors[/green]")

    chroma_size = _store.chroma_count()
    rich.print(f"[bold]Chroma collection count: {chroma_size}[/bold]")
    rich.print(f"Expected (hot+warm in SQLite): {total}")
    if chroma_size < total * 0.95:
        rich.print("[red]WARNING: Chroma count is significantly lower than expected[/red]")
    else:
        rich.print("[green]Counts match — rebuild successful[/green]")
