"""Human evaluation web UI — side-by-side comparison and scoring.

A lightweight FastAPI application that serves a local web interface
for comparing experiment outputs and recording human scores against
rubric dimensions.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..manifest import Manifest, load_all_manifests
from ..rubric import Rubric, load_rubric_by_name


STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

STATUS_CLASSES = {
    "success": "status-success",
    "failed": "status-failed",
    "pending": "status-pending",
    "running": "status-running",
}


def _create_jinja_env() -> Environment:
    """Build the Jinja2 environment with auto-escaping."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def create_app(
    experiments_dir: Path,
    rubrics_dir: Path,
    filter_id: str | None = None,
) -> FastAPI:
    """Build the FastAPI application for human evaluation."""

    app = FastAPI(title="hunch_kit evaluation")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    jinja_env = _create_jinja_env()

    # Stash config on the app for route access
    app.state.experiments_dir = Path(experiments_dir)
    app.state.rubrics_dir = Path(rubrics_dir)
    app.state.filter_id = filter_id

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        experiments_path = app.state.experiments_dir
        manifests = load_all_manifests(experiments_path)

        if app.state.filter_id:
            manifests = [m for m in manifests if m.id == app.state.filter_id]
            if len(manifests) == 1:
                return RedirectResponse(f"/eval/{manifests[0].id}")

        template = jinja_env.get_template("index.html")
        html = template.render(
            title="Experiments",
            manifests=manifests,
            status_classes=STATUS_CLASSES,
        )
        return HTMLResponse(html)

    @app.get("/eval/{experiment_id}", response_class=HTMLResponse)
    async def eval_page(experiment_id: str) -> HTMLResponse:
        exp_dir = app.state.experiments_dir / experiment_id
        if not exp_dir.exists():
            logger.warning("Experiment not found: %s", experiment_id)
            error_template = jinja_env.get_template("error.html")
            return HTMLResponse(
                error_template.render(
                    title="Not Found",
                    code=404,
                    message=f"Experiment '{experiment_id}' not found.",
                ),
                status_code=404,
            )

        manifest = Manifest.load(exp_dir)
        rubric = _load_rubric_for(manifest, app.state.rubrics_dir)

        current_output = _read_output(exp_dir, manifest)
        baseline_output = ""
        baseline_meta = ""
        if manifest.baseline:
            baseline_dir = app.state.experiments_dir / manifest.baseline
            if baseline_dir.exists():
                baseline_manifest = Manifest.load(baseline_dir)
                baseline_output = _read_output(baseline_dir, baseline_manifest)
                baseline_meta = _format_meta(baseline_manifest)

        # Prepare scoring data
        dimensions = rubric.human_dimensions() if rubric else []
        dim_names = {d.name for d in dimensions}
        extra_scores = {
            k: v for k, v in manifest.human_scores.items()
            if k not in dim_names
        }

        template = jinja_env.get_template("eval.html")
        html = template.render(
            title=f"Evaluate: {experiment_id}",
            experiment_id=experiment_id,
            manifest=manifest,
            current_output=current_output,
            current_meta=_format_meta(manifest),
            baseline_output=baseline_output,
            baseline_meta=baseline_meta,
            dimensions=dimensions,
            extra_scores=extra_scores,
        )
        return HTMLResponse(html)

    @app.post("/eval/{experiment_id}/score")
    async def submit_score(experiment_id: str, request: Request) -> JSONResponse:
        exp_dir = app.state.experiments_dir / experiment_id
        if not exp_dir.exists():
            return JSONResponse({"error": "Experiment not found"}, status_code=404)

        data = await request.json()
        manifest = Manifest.load(exp_dir)

        scores = data.get("scores", {})
        for key, value in scores.items():
            try:
                manifest.human_scores[key] = float(value)
            except (ValueError, TypeError):
                manifest.human_scores[key] = value

        if data.get("overall_preference"):
            manifest.overall_preference = data["overall_preference"]
        if "notes" in data:
            manifest.notes = data["notes"]

        manifest.save(exp_dir)
        return JSONResponse({"status": "saved", "experiment_id": experiment_id})

    return app


# -- Helpers ------------------------------------------------------------------


def _load_rubric_for(manifest: Manifest, rubrics_dir: Path) -> Rubric | None:
    if not manifest.rubric:
        return None
    return load_rubric_by_name(rubrics_dir, manifest.rubric)


def _read_output(exp_dir: Path, manifest: Manifest) -> str:
    if not manifest.output_path:
        return ""
    output_path = exp_dir / manifest.output_path
    if output_path.exists() and output_path.suffix in (".txt", ".md", ".yaml", ".yml", ".json"):
        try:
            return output_path.read_text(encoding="utf-8")[:50_000]
        except Exception:
            return f"[Could not read: {output_path.name}]"
    elif output_path.exists():
        return f"[Binary file: {output_path.name} — open externally]"
    return ""


def _format_meta(manifest: Manifest) -> str:
    """Build a short metadata string for display."""
    parts = []
    if manifest.duration_seconds is not None:
        parts.append(f"Duration: {manifest.duration_seconds:.1f}s")
    if manifest.provider:
        parts.append(f"Provider: {manifest.provider}")
    return " · ".join(parts)
