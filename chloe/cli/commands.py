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


@app.command("bootstrap-identity")
def bootstrap_identity(
    person_id: int = typer.Option(1, "--person-id", help="Person ID to generate addendum for"),
    skip_self_model: bool = typer.Option(False, "--skip-self-model", help="Skip weekly self-model (~$0.08 Pro Thinking call)"),
    skip_onboarding: bool = typer.Option(False, "--skip-onboarding", help="Skip onboarding re-extraction"),
):
    """Exhaustive identity bootstrap — runs the full weekly pipeline from scratch.

    Phase order:
      1. Onboarding re-extraction  (Flash: inner_aversions, inner_questions, trait_profile, teo_read)
      2. Trait adjudication        (Flash: review evidence, apply weight updates + stale decay)
      3. Weekly self-model         (Pro Thinking: first self-belief + week intention)
      4. Narrative consolidation   (Flash: compress witness entries if ≥5 exist)
      5. Narrative weaver          (Pro Thinking: NarrativeEntry + character addendum if overdue)
      6. Signal extraction         (Flash: narrative → world_beliefs, interest promotions, tensions)
      7. Curiosity question drain  (Flash: generate questions for interests above threshold)
      8. Aesthetic patterns        (Flash: only if ≥10 reactions exist)

    Safe to re-run on a live DB. Requires GEMINI_API_KEY.
    """
    asyncio.run(_bootstrap_identity_async(person_id, skip_self_model=skip_self_model, skip_onboarding=skip_onboarding))


async def _bootstrap_identity_async(
    person_id: int,
    skip_self_model: bool = False,
    skip_onboarding: bool = False,
) -> None:
    import rich
    from chloe.state.db import migrate, seed_primary_persons, get_connection

    migrate()
    seed_primary_persons()

    rich.print(f"\n[bold]Bootstrap identity[/bold] — person_id={person_id}")
    rich.print("[dim]Running full weekly pipeline. Requires GEMINI_API_KEY.[/dim]")

    results: dict = {}

    def section(label: str) -> None:
        rich.print(f"\n[bold cyan]── {label} ──[/bold cyan]")

    # ── 1: Onboarding re-extraction ───────────────────────────────────────────
    section("1/8  Onboarding re-extraction")
    if skip_onboarding:
        rich.print("[dim]skipped (--skip-onboarding)[/dim]")
        results["onboarding"] = {"skipped": True}
    else:
        try:
            conn = get_connection()
            rows = conn.execute(
                "SELECT text FROM memories WHERE source='onboarding' ORDER BY id ASC"
            ).fetchall()
            if not rows:
                rich.print("[yellow]No onboarding memories — skipping[/yellow]")
                results["onboarding"] = {"skipped": True, "reason": "no_onboarding_memories"}
            else:
                from chloe.identity.onboarding import run_extraction
                qa_text = "\n\n".join(
                    r["text"].replace("Teo told me: ", "A: ") for r in rows
                )
                r = await run_extraction(qa_text, conn)
                if r.get("extraction") == "failed":
                    rich.print("[yellow]Extraction returned nothing (no API key?)[/yellow]")
                else:
                    n_knowledge = r.get("knowledge_statements", 0)
                    n_people = len(r.get("people_found") or [])
                    n_created = len(r.get("people_created") or [])
                    n_aversions = len(r.get("aversions") or [])
                    n_threads = len(r.get("open_threads") or [])
                    rich.print(
                        f"[green]knowledge={n_knowledge}, people={n_people} ({n_created} new),"
                        f" aversions={n_aversions}, threads={n_threads}[/green]"
                    )
                results["onboarding"] = r
        except Exception as exc:
            rich.print(f"[red]Onboarding re-extraction failed: {exc}[/red]")
            results["onboarding"] = {"error": str(exc)}

    # ── 2: Trait adjudication ─────────────────────────────────────────────────
    section("2/8  Trait adjudication")
    try:
        from chloe.reflect.weekly import run_trait_adjudication
        r = await run_trait_adjudication()
        if r.get("skipped"):
            rich.print("[yellow]Skipped (no recent evidence)[/yellow]")
        else:
            rich.print(
                f"[green]weight_updates={r.get('weight_updates', 0)},"
                f" new_patterns={r.get('new_patterns', 0)}[/green]"
            )
        results["trait_adjudication"] = r
    except Exception as exc:
        rich.print(f"[red]Trait adjudication failed: {exc}[/red]")
        results["trait_adjudication"] = {"error": str(exc)}

    # ── 3: Weekly self-model ──────────────────────────────────────────────────
    section("3/8  Weekly self-model (Pro Thinking)")
    if skip_self_model:
        rich.print("[dim]Skipped (--skip-self-model)[/dim]")
        results["self_model"] = {"skipped": True}
    else:
        try:
            from chloe.identity.self_model import run_weekly_self_model
            r = await run_weekly_self_model()
            if r:
                rich.print(f"[green]belief_id={r['belief_id']}, goal_id={r['goal_id']}[/green]")
            else:
                rich.print("[yellow]No result (no API key?)[/yellow]")
            results["self_model"] = r or {"error": "no_result"}
        except Exception as exc:
            rich.print(f"[red]Self-model failed: {exc}[/red]")
            results["self_model"] = {"error": str(exc)}

    # ── 4: Narrative consolidation ────────────────────────────────────────────
    section("4/8  Narrative consolidation")
    try:
        from chloe.reflect.weekly import run_narrative_consolidation
        r = await run_narrative_consolidation()
        if r.get("skipped"):
            rich.print("[yellow]Skipped (fewer than 5 witness entries)[/yellow]")
        else:
            rich.print(
                f"[green]consolidated_id={r.get('consolidated_id')},"
                f" archived={r.get('archived')} entries[/green]"
            )
        results["narrative_consolidation"] = r
    except Exception as exc:
        rich.print(f"[red]Narrative consolidation failed: {exc}[/red]")
        results["narrative_consolidation"] = {"error": str(exc)}

    # ── 5: Narrative weaver + character addendum ──────────────────────────────
    section("5/8  Narrative weaver (Pro Thinking) + character addendum")
    try:
        from chloe.identity.narrative_weaver import weave_narrative
        r = await weave_narrative()
        if "error" in r:
            rich.print(f"[yellow]Narrative weaver: {r}[/yellow]")
        else:
            chapter_note = " [chapter transition]" if r.get("chapter_transition") else ""
            addendum_note = " + addendum" if r.get("addendum_triggered") else ""
            rich.print(f"[green]Entry: \"{r.get('period_label', '?')}\"{chapter_note}{addendum_note}[/green]")
        results["narrative"] = r
    except Exception as exc:
        rich.print(f"[red]Narrative weaver failed: {exc}[/red]")
        results["narrative"] = {"error": str(exc)}

    # If addendum wasn't triggered by the weaver (e.g. it errored), generate one directly
    if not results.get("narrative", {}).get("addendum_triggered"):
        try:
            from chloe.identity.character_addendum import update_addendum
            narrative_r = results.get("narrative", {})
            ctx = (
                f"{narrative_r.get('period_label', '')}: "
                f"{narrative_r.get('felt_texture', '')}".strip(": ")
                if isinstance(narrative_r, dict) else ""
            )
            body = await update_addendum(person_id=person_id, narrative_context=ctx)
            if body:
                rich.print(f"[green]  Addendum written ({len(body)} chars)[/green]")
            else:
                rich.print("[yellow]  Addendum: no output (no API key?)[/yellow]")
        except Exception as exc:
            rich.print(f"[red]  Addendum failed: {exc}[/red]")

    # ── 6: Signal extraction ──────────────────────────────────────────────────
    section("6/8  Signal extraction")
    try:
        from chloe.reflect.weekly import run_signal_extraction
        r = await run_signal_extraction()
        if r.get("skipped"):
            rich.print("[yellow]Skipped (no narrative entries yet)[/yellow]")
        else:
            applied = r.get("applied", {})
            rich.print(
                f"[green]beliefs={applied.get('beliefs', 0)},"
                f" promotions={applied.get('promotions', 0)},"
                f" tensions={applied.get('tensions', 0)}[/green]"
            )
        results["signal_extraction"] = r
    except Exception as exc:
        rich.print(f"[red]Signal extraction failed: {exc}[/red]")
        results["signal_extraction"] = {"error": str(exc)}

    # ── 7: Curiosity question backlog ─────────────────────────────────────────
    section("7/8  Curiosity question drain")
    try:
        from chloe.identity.interest_garden import drain_pending_curiosity_questions
        drained = await drain_pending_curiosity_questions()
        if drained:
            rich.print(f"[green]Generated {drained} curiosity question(s)[/green]")
        else:
            rich.print("[dim]No pending questions[/dim]")
        results["curiosity_questions"] = {"drained": drained}
    except Exception as exc:
        rich.print(f"[red]Curiosity drain failed: {exc}[/red]")
        results["curiosity_questions"] = {"error": str(exc)}

    # ── 8: Aesthetic patterns ─────────────────────────────────────────────────
    section("8/8  Aesthetic patterns")
    try:
        from chloe.identity.aesthetics import run_pattern_review, MIN_REACTIONS_FOR_PATTERN
        conn = get_connection()
        count_row = conn.execute("SELECT COUNT(*) AS n FROM aesthetic_reactions").fetchone()
        reaction_count = count_row["n"] if count_row else 0
        if reaction_count < MIN_REACTIONS_FOR_PATTERN:
            rich.print(f"[yellow]Only {reaction_count}/{MIN_REACTIONS_FOR_PATTERN} reactions — skipping[/yellow]")
            results["aesthetics"] = {"skipped": True, "count": reaction_count}
        else:
            r = await run_pattern_review()
            if r.get("skipped") or r.get("error"):
                rich.print(f"[yellow]Aesthetic patterns: {r}[/yellow]")
            else:
                rich.print(f"[green]{r.get('patterns', 0)} patterns across {r.get('domains', [])}[/green]")
            results["aesthetics"] = r
    except Exception as exc:
        rich.print(f"[red]Aesthetic patterns failed: {exc}[/red]")
        results["aesthetics"] = {"error": str(exc)}

    # ── Summary ────────────────────────────────────────────────────────────────
    rich.print("\n[bold]Bootstrap complete.[/bold]")
    errors = [k for k, v in results.items() if isinstance(v, dict) and "error" in v]
    skipped = [k for k, v in results.items() if isinstance(v, dict) and v.get("skipped")]
    ok = [k for k in results if k not in errors and k not in skipped]
    if ok:
        rich.print(f"  [green]ok:[/green] {', '.join(ok)}")
    if skipped:
        rich.print(f"  [dim]skipped:[/dim] {', '.join(skipped)}")
    if errors:
        rich.print(f"  [red]errors:[/red] {', '.join(errors)}")


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
