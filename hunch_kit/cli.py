"""hunch_kit CLI — the primary user interface.

Usage:
    hunch init          Scaffold a new experiment
    hunch run <id>      Execute an experiment through its provider
    hunch eval [id]     Launch the evaluation web UI
    hunch list          List all experiments
    hunch lineage [id]  Display experiment genealogy
"""

from __future__ import annotations

from pathlib import Path

import click

from .manifest import (
    Manifest,
    find_experiments,
    load_all_manifests,
    build_lineage,
)


def _resolve_root(ctx: click.Context) -> Path:
    """Resolve the experiments root directory."""
    return Path(ctx.obj.get("root", ".")) / "experiments"


def _resolve_rubrics(ctx: click.Context) -> Path:
    return Path(ctx.obj.get("root", ".")) / "rubrics"


@click.group()
@click.option(
    "--root", "-r",
    default=".",
    help="Project root directory (defaults to current directory).",
)
@click.pass_context
def cli(ctx: click.Context, root: str) -> None:
    """hunch_kit — structured experimentation for iterative creative workflows."""
    ctx.ensure_object(dict)
    ctx.obj["root"] = root


# -- init ---------------------------------------------------------------------


@cli.command()
@click.option("--id", "experiment_id", prompt="Experiment ID", help="Unique experiment identifier.")
@click.option("--hypothesis", prompt="Hypothesis", help="What you expect this change to achieve.")
@click.option("--variable", "variable_changed", prompt="Variable being changed", help="The single variable under test.")
@click.option("--value", "variable_value", prompt="New value for that variable", help="What the variable was changed to.")
@click.option("--baseline", default="", prompt="Baseline experiment (or empty for first)", help="Parent experiment ID.")
@click.option("--provider", default="echo", help="Provider to use for execution.")
@click.option("--rubric", default="", help="Rubric name for evaluation.")
@click.option("--input-path", default="", help="Path to input file (relative to experiment dir).")
@click.pass_context
def init(
    ctx: click.Context,
    experiment_id: str,
    hypothesis: str,
    variable_changed: str,
    variable_value: str,
    baseline: str,
    provider: str,
    rubric: str,
    input_path: str,
) -> None:
    """Scaffold a new experiment."""
    exp_root = _resolve_root(ctx)
    exp_dir = exp_root / experiment_id

    if exp_dir.exists():
        raise click.ClickException(f"Experiment directory already exists: {exp_dir}")

    manifest = Manifest.create(
        experiment_id=experiment_id,
        hypothesis=hypothesis,
        variable_changed=variable_changed,
        variable_value=variable_value,
        baseline=baseline,
        provider=provider,
        rubric=rubric,
        input_path=input_path,
    )

    errors = manifest.validate()
    if errors:
        raise click.ClickException("Validation failed:\n  " + "\n  ".join(errors))

    exp_dir.mkdir(parents=True)
    (exp_dir / "output").mkdir()
    manifest.save(exp_dir)

    # Update parent's children list if a baseline is declared
    if baseline:
        parent_dir = exp_root / baseline
        if parent_dir.exists():
            try:
                parent = Manifest.load(parent_dir)
                if experiment_id not in parent.children:
                    parent.children.append(experiment_id)
                    parent.save(parent_dir)
            except Exception:
                pass

    click.echo(f"Created: {exp_dir.relative_to(Path(ctx.obj['root']))}/")
    click.echo(f"Manifest: {exp_dir / 'experiment.yaml'}")
    click.echo()
    click.echo("Next steps:")
    if not input_path:
        click.echo(f"  1. Add your input file to {exp_dir}/")
        click.echo(f"     Then update input_path in experiment.yaml")
    click.echo(f"  2. Run: hunch run {experiment_id}")
    click.echo(f"  3. Evaluate: hunch eval {experiment_id}")


# -- run ----------------------------------------------------------------------


@cli.command()
@click.argument("experiment_id")
@click.option("--provider", default=None, help="Override the provider declared in the manifest.")
@click.pass_context
def run(ctx: click.Context, experiment_id: str, provider: str | None) -> None:
    """Execute an experiment through its provider."""
    from .runner import run_experiment

    exp_dir = _resolve_root(ctx) / experiment_id
    if not exp_dir.exists():
        raise click.ClickException(f"Experiment not found: {experiment_id}")

    click.echo(f"Running experiment: {experiment_id}")
    result = run_experiment(exp_dir, provider_override=provider)

    if result.status == "success":
        click.echo(f"  Status: {result.status}")
        click.echo(f"  Duration: {result.duration_seconds:.1f}s")
        click.echo(f"  Output length: {len(result.output)} chars")
    else:
        click.echo(f"  Status: {result.status}")
        if result.error:
            click.echo(f"  Error: {result.error}")


# -- eval ---------------------------------------------------------------------


@cli.command()
@click.argument("experiment_id", required=False)
@click.option("--port", default=8888, help="Port for the evaluation server.")
@click.option("--host", default="127.0.0.1", help="Host for the evaluation server.")
@click.pass_context
def eval(ctx: click.Context, experiment_id: str | None, port: int, host: str) -> None:
    """Launch the evaluation web UI."""
    from .evaluation.human import create_app
    import uvicorn

    root = Path(ctx.obj["root"])
    app = create_app(
        experiments_dir=root / "experiments",
        rubrics_dir=root / "rubrics",
        filter_id=experiment_id,
    )

    click.echo(f"Evaluation UI: http://{host}:{port}")
    if experiment_id:
        click.echo(f"Filtered to experiment: {experiment_id}")
    click.echo("Press Ctrl+C to stop.\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


# -- list ---------------------------------------------------------------------


@cli.command("list")
@click.pass_context
def list_experiments(ctx: click.Context) -> None:
    """List all experiments with their status."""
    exp_root = _resolve_root(ctx)
    dirs = find_experiments(exp_root)

    if not dirs:
        click.echo("No experiments found.")
        return

    click.echo(f"{'ID':<30} {'Status':<12} {'Baseline':<20} {'Preference'}")
    click.echo("-" * 80)

    for d in dirs:
        m = Manifest.load(d)
        click.echo(
            f"{m.id:<30} {m.status:<12} {m.baseline or '—':<20} "
            f"{m.overall_preference or '—'}"
        )


# -- lineage ------------------------------------------------------------------


@cli.command()
@click.argument("experiment_id", required=False)
@click.pass_context
def lineage(ctx: click.Context, experiment_id: str | None) -> None:
    """Display experiment genealogy as a tree."""
    exp_root = _resolve_root(ctx)
    manifests = load_all_manifests(exp_root)

    if not manifests:
        click.echo("No experiments found.")
        return

    tree = build_lineage(manifests)

    roots = [
        m.id for m in manifests
        if not m.baseline or m.baseline not in tree
    ]

    if experiment_id:
        if experiment_id not in tree:
            raise click.ClickException(f"Experiment not found: {experiment_id}")
        _print_tree(tree, experiment_id, "")
    else:
        for root_id in sorted(roots):
            _print_tree(tree, root_id, "")


def _print_tree(tree: dict[str, list[str]], node: str, prefix: str) -> None:
    """Recursively print a tree node and its children."""
    click.echo(f"{prefix}{node}")
    children = tree.get(node, [])
    for i, child in enumerate(sorted(children)):
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        _print_tree(tree, child, prefix + connector)
