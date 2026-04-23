"""CLI interface for AutoResearch SDK."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autoresearch import AutoResearch, __version__

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="autoresearch")
def main() -> None:
    """AutoResearch — autonomous experiment loop CLI.

    Try ideas, keep what works, discard what doesn't, never stop.
    """
    pass


@main.command()
@click.option("--name", "-n", required=True, help="Experiment name")
@click.option("--metric", "-m", required=True, help="Primary metric name")
@click.option("--direction", "-d", type=click.Choice(["lower", "higher"]), default="lower",
              help="Optimize direction (lower/higher is better)")
@click.option("--unit", "-u", default="", help="Metric unit (ms, s, bytes, etc.)")
@click.option("--reset", is_flag=True, help="Reset existing experiment")
@click.option("--cwd", "-C", type=click.Path(exists=True), default=".", help="Working directory")
def init(name: str, metric: str, direction: str, unit: str, reset: bool, cwd: str) -> None:
    """Initialize a new experiment."""
    ar = AutoResearch(
        name=name,
        metric=metric,
        direction=direction,
        unit=unit,
        cwd=Path(cwd),
    )
    ar.init(reset=reset)
    console.print(f"[green]✓[/green] Experiment '{name}' initialized (direction: {direction})")
    console.print(f"  Metric: {metric} ({unit}) — {direction} is better")
    console.print(f"  Results: {ar.cwd / ar.RESULTS_LOG}")


@main.command()
@click.option("--command", "-c", required=True, help="Benchmark command to run")
@click.option("--timeout", "-t", default=600, help="Timeout in seconds")
@click.option("--cwd", "-C", type=click.Path(exists=True), default=".", help="Working directory")
def run(command: str, timeout: int, cwd: str) -> None:
    """Run a benchmark command and capture metrics."""
    ar = AutoResearch(cwd=Path(cwd))
    if not ar.is_initialized:
        console.print("[red]✗[/red] Not initialized. Run 'autoresearch init' first.")
        sys.exit(1)

    console.print(f"[dim]Running: {command}[/dim]")
    result = ar.run(command, timeout=timeout)

    if result.timed_out:
        console.print(f"[red]✗ Timed out after {result.duration_seconds:.1f}s[/red]")
    elif result.crashed:
        console.print(f"[red]✗ Crashed (exit code: {result.exit_code})[/red]")
    else:
        console.print(f"[green]✓ Passed in {result.duration_seconds:.1f}s[/green]")

    # Show metrics
    from autoresearch.core.metrics import parse_metric_lines
    metrics = parse_metric_lines(result.combined_output)
    if metrics:
        table = Table(title="Metrics")
        table.add_column("Name", style="cyan")
        table.add_column("Value", style="green")
        for name, value in metrics.items():
            table.add_row(name, f"{value:.2f}")
        console.print(table)

    # Show tail output
    if result.tail_output:
        console.print(Panel(result.tail_output, title="Output (tail)", border_style="dim"))

    console.print(f"\n[yellow]→ Call 'autoresearch log' to record this run[/yellow]")


@main.command()
@click.option("--description", "-d", default="", help="What was changed")
@click.option("--status", "-s", type=click.Choice(["keep", "discard", "crash"]), default=None,
              help="Run status (default: keep if passed)")
@click.option("--idea", "-i", default="", help="Idea for future (only with discard)")
@click.option("--cwd", "-C", type=click.Path(exists=True), default=".", help="Working directory")
def log(description: str, status: str | None, idea: str, cwd: str) -> None:
    """Log the pending run result."""
    ar = AutoResearch(cwd=Path(cwd))
    if not ar.has_pending:
        console.print("[red]✗ No pending run. Run 'autoresearch run' first.[/red]")
        sys.exit(1)

    if idea and status != "discard":
        console.print("[yellow]⚠ Idea only makes sense with --status discard[/yellow]")

    if status == "discard" and idea:
        logged = ar.discard_with_idea(description or "Discarded", idea)
    else:
        logged = ar.log(description=description, status=status)

    emoji = {"keep": "✅", "discard": "❌", "crash": "💥"}.get(logged.status.value, "?")
    console.print(f"{emoji} Run #{logged.run} logged — {logged.status.value}")
    console.print(f"  Metric: {logged.metric:.2f} | Baseline: {ar.baseline_metric}")
    console.print(f"  Best: {ar.best_metric}")

    from autoresearch.core.confidence import describe_confidence
    console.print(f"  {describe_confidence(ar.confidence)}")


@main.command()
@click.option("--cwd", "-C", type=click.Path(exists=True), default=".", help="Working directory")
def status(cwd: str) -> None:
    """Show experiment status."""
    ar = AutoResearch(cwd=Path(cwd))
    state = ar.get_status()

    if not state["initialized"]:
        console.print("[yellow]No active experiment.[/yellow]")
        return

    # Header
    panel_content = (
        f"[bold]{state['name']}[/bold]\n"
        f"Metric: {state['metric_name']} ({state['metric_unit']}) — {state['best_direction']} is better\n"
        f"Segment: {state['segment']} | Runs: {state['total_runs']} (this segment: {state['runs_in_segment']})"
    )
    console.print(Panel(panel_content, title="AutoResearch Status", border_style="blue"))

    # Metrics
    console.print(f"  Baseline:  {state['baseline_metric']}")
    console.print(f"  Best:      {state['best_metric']}")
    from autoresearch.core.confidence import describe_confidence
    console.print(f"  {describe_confidence(state['confidence'])}")

    if state["has_pending"]:
        console.print(f"  [yellow]⚠ Pending run exists (run 'autoresearch log')[/yellow]")

    # Recent runs
    recent = ar.runs[-10:]
    if recent:
        table = Table(title="Recent Runs")
        table.add_column("#", style="dim")
        table.add_column("Metric", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Description")
        for r in recent:
            emoji = {"keep": "✅", "discard": "❌", "crash": "💥"}.get(r.status.value, "?")
            table.add_row(str(r.run), f"{r.metric:.2f}", f"{emoji} {r.status.value}",
                         r.description[:50])
        console.print(table)


@main.command()
@click.option("--cwd", "-C", type=click.Path(exists=True), default=".", help="Working directory")
def export(cwd: str) -> None:
    """Export experiment results as JSON."""
    ar = AutoResearch(cwd=Path(cwd))
    state = ar.get_status()
    state["runs"] = [r.to_dict() for r in ar.runs]
    console.print_json(json.dumps(state))


@main.command()
@click.option("--cwd", "-C", type=click.Path(exists=True), default=".", help="Working directory")
@click.option("--force", is_flag=True, help="Reset without confirmation")
def reset(cwd: str, force: bool) -> None:
    """Reset the experiment."""
    if not force:
        if not click.confirm("Reset all experiment data?"):
            return
    ar = AutoResearch(cwd=Path(cwd))
    ar.reset()
    console.print("[green]✓ Experiment reset.[/green]")


if __name__ == "__main__":
    main()
