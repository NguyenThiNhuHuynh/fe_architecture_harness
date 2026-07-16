"""`frontforge` — the only user-facing interface for v1 (no web UI yet)."""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from frontforge.config.stages import StageRegistry
from frontforge.core.human_review import CliHumanReviewHook
from frontforge.core.lock import RunLockError
from frontforge.core.logger import configure_logging
from frontforge.core.orchestrator import Orchestrator
from frontforge.core.tracing import configure_metrics, configure_tracing, shutdown_metrics
from frontforge.core.session import RunSession
from frontforge.providers.claude_cli import ClaudeCliProvider
from frontforge.shared.types import StageStatus
from frontforge.shared.utils import write_json
from frontforge.tools.figma_tool import extract_file_key

app = typer.Typer(help="FrontForge — AI UI-Architecture Harness (DAG-orchestrated agents).")
stage_app = typer.Typer(help="Inspect or act on a single stage.")
app.add_typer(stage_app, name="stage")

console = Console()


@app.command()
def init(
    project: Path = typer.Argument(..., help="Project workspace directory (created if missing)."),
    requirement: str = typer.Option(None, "--requirement", "-r", help="Raw requirement text."),
    from_file: Path = typer.Option(
        None, "--from-file", "-f", help="YAML file with a `raw_requirement` key."
    ),
    figma_url: str = typer.Option(
        None,
        "--figma-url",
        help=(
            "Optional Figma file URL — the design_analysis stage will fetch its "
            "pages/styles/components (needs FIGMA_ACCESS_TOKEN set in the environment)."
        ),
    ),
):
    """Scaffold .harness/ and seed the pipeline with the raw user requirement."""
    session = RunSession.at(project)
    session.scaffold()

    if from_file is not None:
        data = yaml.safe_load(from_file.read_text(encoding="utf-8"))
        raw_requirement = data["raw_requirement"]
        figma_url = figma_url or data.get("figma_url")
    elif requirement is not None:
        raw_requirement = requirement
    else:
        raw_requirement = typer.prompt("Describe what you want to build")

    seed = {"raw_requirement": raw_requirement}
    if figma_url:
        try:
            extract_file_key(figma_url)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="--figma-url") from exc
        if not os.environ.get("FIGMA_ACCESS_TOKEN"):
            console.print(
                "[yellow]Warning:[/yellow] --figma-url was given but FIGMA_ACCESS_TOKEN is not "
                "set in this environment — export it before `frontforge run`, or the "
                "design_analysis stage will fail."
            )
        seed["figma_url"] = figma_url
    write_json(session.seed_file, seed)
    console.print(f"[green]Initialized[/green] project workspace at {session.harness_dir}")


@app.command()
def run(
    project: Path = typer.Argument(..., help="Project workspace directory."),
    only: str = typer.Option(None, "--only", help="Run exactly one stage."),
    to: str = typer.Option(None, "--to", help="Run every stage needed to reach this one."),
    claude_bin: str = typer.Option("claude", help="Path to the claude CLI binary."),
    max_retries: int = typer.Option(2, help="Max retries per stage on verification failure."),
    review: bool = typer.Option(
        False,
        "--review/--no-review",
        help=(
            "Pause after every stage (except quality_review) to confirm/reject/give "
            "feedback, and after quality_review to approve an auto-fix pass. Off by "
            "default so unattended runs behave exactly as before."
        ),
    ),
    max_budget_usd: float = typer.Option(
        None,
        "--max-budget-usd",
        help="Per-call spending cap passed straight to `claude -p --max-budget-usd`.",
    ),
    max_total_cost_usd: float = typer.Option(
        None,
        "--max-total-cost-usd",
        help="Pipeline-wide spending cap — stop starting new stages once cumulative cost crosses this.",
    ),
):
    """Run the pipeline (or a subset of it) via the DAG orchestrator."""
    session = RunSession.at(project)
    session.scaffold()
    run_id = uuid.uuid4().hex[:8]
    configure_logging(session.logs_dir, run_id)
    configure_tracing(session.logs_dir, run_id)
    configure_metrics(session.logs_dir, run_id)

    provider = ClaudeCliProvider(claude_bin=claude_bin, max_budget_usd=max_budget_usd)
    human_review = CliHumanReviewHook() if review else None
    orchestrator = Orchestrator(
        session,
        provider,
        max_retries=max_retries,
        run_id=run_id,
        human_review=human_review,
        max_total_cost_usd=max_total_cost_usd,
    )

    console.print(f"[bold]Running pipeline[/bold] for {project} (run {run_id})")
    try:
        states = asyncio.run(orchestrator.run_all(only=only, to=to))
    except RunLockError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        # Metrics are periodically exported (default every 5s) — without an
        # explicit flush here, a short pipeline run could exit before the
        # last batch is ever exported.
        shutdown_metrics()
    _print_status_table(states)
    _print_summary(states)
    _notify_completion(states)


@app.command()
def status(project: Path = typer.Argument(..., help="Project workspace directory.")):
    """Show the status of every stage."""
    session = RunSession.at(project)
    if not session.state_file.exists():
        console.print("[yellow]No state yet — run `frontforge init` and `frontforge run` first.[/yellow]")
        raise typer.Exit(code=1)
    from frontforge.core.state_store import StateStore

    states = StateStore(session).all()
    _print_status_table(states)


@app.command()
def reset(
    project: Path = typer.Argument(..., help="Project workspace directory."),
    stage: str = typer.Option(..., "--stage", help="Stage to mark dirty (cascades to dependents)."),
):
    """Mark a stage and everything downstream of it as dirty."""
    session = RunSession.at(project)
    registry = StageRegistry()
    from frontforge.core.state_store import StateStore

    state_store = StateStore(session)
    affected = state_store.mark_dirty_cascade(stage, registry.dependents_of)
    console.print(f"Marked dirty: {', '.join(affected)}")


@app.command()
def stats(project: Path = typer.Argument(..., help="Project workspace directory.")):
    """Cross-run stats from every logs-*.jsonl log: per-stage success rate,
    duration percentiles (p50/p95/p99), total cost, verification-failure,
    mark_dirty and HITL-ask counts — folded across all past runs, not just
    the most recent one."""
    session = RunSession.at(project)
    from frontforge.core.stats import compute_stage_stats, read_all_events

    events = read_all_events(session.logs_dir)
    if not events:
        console.print("[yellow]No event logs found yet — run `frontforge run` first.[/yellow]")
        raise typer.Exit(code=1)

    per_stage = compute_stage_stats(events)
    run_count = len(list(session.logs_dir.glob("logs-*.jsonl")))

    table = Table(title=f"Cross-run stats ({run_count} run log(s))")
    table.add_column("Stage")
    table.add_column("Runs")
    table.add_column("Success rate")
    table.add_column("p50")
    table.add_column("p95")
    table.add_column("p99")
    table.add_column("Total cost")
    table.add_column("Verify fails")
    table.add_column("mark_dirty")
    table.add_column("HITL asks")

    registry = StageRegistry()
    for stage_id in registry.all_ids():
        s = per_stage.get(stage_id)
        if s is None:
            continue
        success_rate = f"{s.success_rate * 100:.0f}%" if s.success_rate is not None else ""
        table.add_row(
            stage_id,
            str(s.total_runs),
            success_rate,
            _format_duration(s.percentile(50)),
            _format_duration(s.percentile(95)),
            _format_duration(s.percentile(99)),
            _format_cost(s.total_cost_usd),
            str(s.verification_failures),
            str(s.mark_dirty_count),
            str(s.hitl_decisions),
        )
    console.print(table)

    total_cost = sum(s.total_cost_usd for s in per_stage.values())
    console.print(f"[bold]Total cost across all runs:[/bold] {_format_cost(total_cost)}")


@stage_app.command("show")
def stage_show(
    stage: str = typer.Argument(..., help="Stage id."),
    project: Path = typer.Option(..., "--project", help="Project workspace directory."),
):
    """Print the stored output of a single stage."""
    session = RunSession.at(project)
    from frontforge.core.state_store import StateStore

    state_store = StateStore(session)
    output = state_store.load_output(stage)
    if output is None:
        console.print(f"[yellow]No stored output for stage {stage!r} yet.[/yellow]")
        raise typer.Exit(code=1)
    console.print_json(data=output)


def _print_status_table(states: dict) -> None:
    table = Table(title="Pipeline status")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Attempts")
    table.add_column("Duration")
    table.add_column("Cost")
    table.add_column("Updated at")
    table.add_column("Error")

    registry = StageRegistry()
    for stage_id in registry.all_ids():
        state = states.get(stage_id)
        if state is None:
            status_text, attempts, duration, cost, updated_at, error = "pending", "0", "", "", "", ""
        else:
            status_text = state.status.value if hasattr(state.status, "value") else state.status
            attempts = str(state.attempts)
            duration = _format_duration(state.duration_ms)
            cost = _format_cost(state.cost_usd)
            updated_at = str(state.updated_at) if state.updated_at else ""
            error = state.error or ""
        color = {
            StageStatus.DONE.value: "green",
            StageStatus.FAILED.value: "red",
            StageStatus.DIRTY.value: "yellow",
            StageStatus.RUNNING.value: "cyan",
        }.get(status_text, "white")
        table.add_row(
            stage_id, f"[{color}]{status_text}[/{color}]", attempts, duration, cost, updated_at, error
        )

    console.print(table)


def _format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return ""
    return f"{duration_ms / 1000:.1f}s"


def _format_cost(cost_usd: float | None) -> str:
    if cost_usd is None:
        return ""
    return f"${cost_usd:.4f}"


def _print_summary(states: dict) -> None:
    """Total time/cost across the run just performed, plus the most
    expensive and slowest stage — the numbers ProviderResult.cost_usd
    already carried but nothing used to surface."""
    known = [s for s in states.values() if s.duration_ms is not None or s.cost_usd is not None]
    if not known:
        return

    total_duration_ms = sum(s.duration_ms or 0 for s in known)
    total_cost = sum(s.cost_usd or 0.0 for s in known)
    slowest = max(known, key=lambda s: s.duration_ms or 0)
    priciest = max(known, key=lambda s: s.cost_usd or 0.0)

    table = Table(title="Run summary")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Total duration", _format_duration(total_duration_ms))
    table.add_row("Total cost", _format_cost(total_cost))
    table.add_row("Slowest stage", f"{slowest.stage_id} ({_format_duration(slowest.duration_ms)})")
    table.add_row("Most expensive stage", f"{priciest.stage_id} ({_format_cost(priciest.cost_usd)})")
    console.print(table)


def _notify_completion(states: dict) -> None:
    """Pipelines run unattended for 20-40+ minutes — a visible banner (and a
    terminal bell) matters since nobody may be watching the console."""
    failed = [sid for sid, s in states.items() if s.status == StageStatus.FAILED]
    if failed:
        console.print(f"\a[bold red]Pipeline finished with failures:[/bold red] {', '.join(failed)}")
    else:
        console.print("\a[bold green]Pipeline finished — all requested stages are DONE.[/bold green]")


if __name__ == "__main__":
    app()
