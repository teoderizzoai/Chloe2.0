from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import typer

app = typer.Typer(name="chloe", help="Chloe management CLI")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PID_FILE = _REPO_ROOT / ".chloe.pid"
_LOG_FILE = _REPO_ROOT / "chloe-server.log"


@app.command("start")
def start(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev mode)"),
):
    """Start the Chloe server in the background."""
    if _PID_FILE.exists():
        pid = int(_PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            typer.echo(f"[chloe] Already running (PID {pid}). Use 'chloe stop' first.")
            raise typer.Exit(1)
        except ProcessLookupError:
            _PID_FILE.unlink()

    cmd = [
        sys.executable, "-m", "uvicorn",
        "chloe.app:create_app",
        "--factory",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")

    log = open(_LOG_FILE, "a")
    proc = subprocess.Popen(
        cmd,
        cwd=_REPO_ROOT,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    _PID_FILE.write_text(str(proc.pid))
    typer.echo(f"[chloe] Started (PID {proc.pid}). Logs: {_LOG_FILE}")


@app.command("stop")
def stop():
    """Stop the running Chloe server."""
    if not _PID_FILE.exists():
        typer.echo("[chloe] Not running (no PID file).")
        raise typer.Exit(1)

    pid = int(_PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        _PID_FILE.unlink()
        typer.echo(f"[chloe] Stopped (PID {pid}).")
    except ProcessLookupError:
        _PID_FILE.unlink()
        typer.echo(f"[chloe] Process {pid} not found; removed stale PID file.")


@app.command("status")
def status():
    """Show whether the Chloe server is running."""
    if not _PID_FILE.exists():
        typer.echo("[chloe] Not running.")
        return

    pid = int(_PID_FILE.read_text().strip())
    try:
        os.kill(pid, 0)
        typer.echo(f"[chloe] Running (PID {pid}). Logs: {_LOG_FILE}")
    except ProcessLookupError:
        _PID_FILE.unlink()
        typer.echo("[chloe] Not running (stale PID file removed).")


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


@app.command("simulate-day")
def simulate_day_cmd(
    hours: int = typer.Option(24, "--hours", help="Simulated hours to run (use 72 for 3 days, etc.)"),
    step: int = typer.Option(30, "--step", help="Minutes per simulated step"),
    start: str = typer.Option("", "--start", help="ISO start time (default: today 00:00)"),
    sim_db: str = typer.Option("chloe.sim.db", "--sim-db", help="Simulator DB path"),
    source_db: str = typer.Option("chloe.db", "--source-db", help="Source DB to copy. Ignored when --clean is set."),
    no_script: bool = typer.Option(False, "--no-script", help="Skip the default scripted chat/affect events"),
    quiet: bool = typer.Option(False, "--quiet", help="Don't print per-step lines"),
    clean: bool = typer.Option(False, "--clean", help="Start from a blank schema-only DB (no real memories bleed in). Recommended for multi-day runs."),
):
    """Fast-forward Chloe through a simulated day or multi-day run.

    Examples:
      chloe simulate-day                         # 24h from prod DB copy
      chloe simulate-day --clean                 # 24h clean slate
      chloe simulate-day --clean --hours 72      # 3 days, script repeats daily
    """
    from chloe.sim.day import simulate_day
    from datetime import datetime as _dt

    start_dt = _dt.fromisoformat(start) if start else _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
    src = Path(source_db) if (source_db and not clean) else None
    asyncio.run(simulate_day(
        sim_db=Path(sim_db),
        source_db=src,
        start=start_dt,
        hours=hours,
        step_minutes=step,
        use_default_script=not no_script,
        print_each_step=not quiet,
        clean=clean,
    ))
