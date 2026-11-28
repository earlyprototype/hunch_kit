"""Human evaluation web UI — side-by-side comparison and scoring.

A lightweight FastAPI application that serves a local web interface
for comparing experiment outputs and recording human scores against
rubric dimensions.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from yaml import YAMLError

from ..manifest import (
    MANIFEST_FILENAME, Manifest, ManifestConflict, load_all_manifests, preference_revision, reserve_outputs,
    save_human_scores, save_overall_preference,
)
from ..project import ProjectConfig
from ..rubric import Rubric, load_rubric_by_name
from .blind import BlindPreviews, Fingerprint, UnverifiedAttribution, identifying_patterns, local_file


STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"

STATUS_CLASSES = {
    "success": "status-success",
    "failed": "status-failed",
    "pending": "status-pending",
    "running": "status-running",
}


@dataclass
class BlindComparison:
    child: str
    baseline: str
    child_slot: str
    output_hashes: tuple[Fingerprint, ...] = ()
    initial_preference: str = ""
    preference_revision: bytes = b""
    vote: str = ""

    def preference(self, vote: str) -> str:
        if vote == "tie":
            return "equivalent"
        return "better" if vote == self.child_slot else "worse"


class ComparisonUnavailable(HTTPException):
    """An error the reviewer can see before the mapping is revealed."""


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
    *,
    blind: bool = False,
) -> FastAPI:
    """Build the FastAPI application for human evaluation."""

    app = FastAPI(title="hunch_kit evaluation")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    jinja_env = _create_jinja_env()
    comparisons: dict[str, BlindComparison] = {}

    @cache
    def preview_store() -> BlindPreviews:
        try:
            config = ProjectConfig.load(Path(experiments_dir).parent)
        except (OSError, ValueError, AttributeError, YAMLError) as exc:
            raise ComparisonUnavailable(409, "The blind image mask configuration is unreadable.") from exc
        return BlindPreviews(config.blind_image_masks)

    # Stash config on the app for route access
    app.state.experiments_dir = Path(experiments_dir)
    app.state.rubrics_dir = Path(rubrics_dir)
    app.state.filter_id = filter_id

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        if blind:
            return start_comparison(filter_id)
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
        current_input = _read_input(exp_dir, manifest)
        baseline_output = ""
        baseline_meta = ""
        baseline_input = ""
        if manifest.baseline:
            baseline_dir = app.state.experiments_dir / manifest.baseline
            if baseline_dir.exists():
                baseline_manifest = Manifest.load(baseline_dir)
                baseline_output = _read_output(baseline_dir, baseline_manifest)
                baseline_meta = _format_meta(baseline_manifest)
                baseline_input = _read_input(baseline_dir, baseline_manifest)

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
            current_input=current_input,
            current_meta=_format_meta(manifest),
            baseline_output=baseline_output,
            baseline_input=baseline_input,
            baseline_meta=baseline_meta,
            dimensions=dimensions,
            extra_scores=extra_scores,
        )
        return HTMLResponse(html)

    @app.exception_handler(ComparisonUnavailable)
    async def comparison_error(request: Request, exc: ComparisonUnavailable) -> HTMLResponse:
        template = jinja_env.get_template("error.html")
        return HTMLResponse(template.render(
            title="Comparison unavailable", code=exc.status_code, message=exc.detail,
        ), status_code=exc.status_code)

    def comparison_directory(experiment_id: str | None) -> Path:
        if experiment_id and Path(experiment_id).name == experiment_id and experiment_id != "..":
            try:
                local_file(app.state.experiments_dir, f"{experiment_id}/{MANIFEST_FILENAME}")
                return app.state.experiments_dir / experiment_id
            except (OSError, ValueError):
                pass
        raise ComparisonUnavailable(404, "This comparison requires a child experiment with a baseline.")

    def comparison_manifest(experiment_id: str | None) -> Manifest:
        manifest = Manifest.load(comparison_directory(experiment_id))
        if manifest.id != experiment_id:
            raise ComparisonUnavailable(409, "The experiment changed. Start a new review.")
        return manifest

    @app.get("/blind")
    def start_comparison(experiment_id: str | None = None) -> RedirectResponse:
        child = comparison_manifest(experiment_id)
        baseline = comparison_manifest(child.baseline)
        if child.id == baseline.id:
            raise ComparisonUnavailable(409, "This comparison requires two distinct experiments.")
        key = secrets.token_urlsafe(18)
        comparisons[key] = BlindComparison(child.id, baseline.id, secrets.choice(("A", "B")))
        return RedirectResponse(f"/blind/{key}", status_code=303)

    def get_comparison(key: str) -> BlindComparison:
        if key not in comparisons:
            raise ComparisonUnavailable(404, "This comparison has expired. Start a new review.")
        return comparisons[key]

    def check_outputs(comparison: BlindComparison) -> tuple[Manifest, Manifest]:
        child = comparison_manifest(comparison.child)
        generation = preference_revision(app.state.experiments_dir / child.id)
        if child.baseline != comparison.baseline:
            raise ComparisonUnavailable(409, "The baseline changed. Start a new review.")
        baseline = comparison_manifest(comparison.baseline)
        if "running" in (child.status, baseline.status):
            raise ComparisonUnavailable(409, "An experiment is running. Review the outputs when it finishes.")
        if {child.status, baseline.status}.intersection({"failed", "timeout"}):
            raise ComparisonUnavailable(409, "An experiment failed or timed out. Run it again before voting.")
        ordered = (child, baseline) if comparison.child_slot == "A" else (baseline, child)
        patterns = identifying_patterns(child, baseline)
        previews = preview_store()
        try:
            hashes = tuple(
                previews.read(
                    app.state.experiments_dir / manifest.id, manifest.output_path,
                    attribution=_text_attribution(manifest), patterns=patterns,
                    declared_provider=manifest.provider,
                )
                for manifest in ordered
            )
        except UnverifiedAttribution as exc:
            raise ComparisonUnavailable(409, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise ComparisonUnavailable(409, "Both experiments need readable text or a still image.") from exc
        if not comparison.output_hashes:
            comparison.output_hashes = hashes
            comparison.initial_preference = child.overall_preference
            comparison.preference_revision = generation
        elif comparison.output_hashes != hashes:
            raise ComparisonUnavailable(409, "An output changed. Start a new review.")
        elif comparison.initial_preference != child.overall_preference or comparison.preference_revision != generation:
            raise ComparisonUnavailable(409, "The recorded preference changed. Start a new review.")
        return child, baseline

    @app.get("/blind/{key}", response_class=HTMLResponse)
    async def blind_page(key: str) -> HTMLResponse:
        comparison = get_comparison(key)
        if not comparison.vote:
            check_outputs(comparison)
        outputs = {
            slot: preview_store().recent.get(digest)
            for slot, digest in zip(("A", "B"), comparison.output_hashes)
        }
        template = jinja_env.get_template("blind.html")
        return HTMLResponse(template.render(
            title="Blind comparison", outputs=outputs, key=key,
            result=comparison if comparison.vote else None,
        ), headers={"Cache-Control": "no-store"})

    @app.post("/blind/{key}/vote/{vote}")
    async def blind_vote(key: str, vote: Literal["A", "B", "tie"]) -> RedirectResponse:
        comparison = get_comparison(key)
        if not comparison.vote:
            if not comparison.output_hashes:
                raise ComparisonUnavailable(409, "View both outputs before choosing.")
            directories = (comparison_directory(comparison.child), comparison_directory(comparison.baseline))
            try:
                with reserve_outputs(*directories):
                    child, baseline = check_outputs(comparison)
                    save_overall_preference(
                        app.state.experiments_dir / child.id, comparison.preference(vote),
                        revision=child.revision,
                        baseline_snapshot=(directories[1] / MANIFEST_FILENAME, baseline.revision),
                        reviewed_preference=comparison.preference_revision,
                    )
            except ManifestConflict as exc:
                raise ComparisonUnavailable(409, str(exc)) from exc
            comparison.vote = vote
        return RedirectResponse(f"/blind/{key}", status_code=303)

    @app.post("/eval/{experiment_id}/score")
    async def submit_score(experiment_id: str, request: Request) -> JSONResponse:
        exp_dir = app.state.experiments_dir / experiment_id
        if not exp_dir.exists():
            return JSONResponse({"error": "Experiment not found"}, status_code=404)

        data = await request.json()
        try:
            revision = save_human_scores(exp_dir, data)
        except ManifestConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({
            "status": "saved", "experiment_id": experiment_id, "revision": revision,
        })

    return app


# -- Helpers ------------------------------------------------------------------


def _text_attribution(manifest: Manifest) -> tuple[str, str]:
    """Use the recorded producer when the runner used a provider override."""
    provider = (manifest.provider_metadata or {}).get("provider") or manifest.provider
    if not isinstance(provider, str) or not provider:
        return "", ""
    prefix = (manifest.provider_config or {}).get("prefix")
    if isinstance(prefix, str) and provider.casefold() in prefix.casefold():
        return provider, prefix
    return provider, f"[{provider}] "


def _load_rubric_for(manifest: Manifest, rubrics_dir: Path) -> Rubric | None:
    if not manifest.rubric:
        return None
    return load_rubric_by_name(rubrics_dir, manifest.rubric)


def _read_input(exp_dir: Path, manifest: Manifest) -> str:
    """Read the experiment input (prompt) as text, if one was recorded."""
    if not manifest.input_path:
        return ""
    input_path = exp_dir / manifest.input_path
    if not input_path.exists():
        return ""
    try:
        return input_path.read_text(encoding="utf-8").strip()[:5_000]
    except Exception:
        return ""


def _read_output(exp_dir: Path, manifest: Manifest) -> str:
    if not manifest.output_path:
        return ""
    output_path = exp_dir / manifest.output_path
    if not output_path.is_file():
        return ""
    suffix = output_path.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        import base64
        import mimetypes

        mime = mimetypes.guess_type(str(output_path))[0] or "image/png"
        image = output_path.read_bytes()
        encoded = base64.b64encode(image).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    if suffix in (".txt", ".md", ".yaml", ".yml", ".json"):
        try:
            return output_path.read_text(encoding="utf-8")[:50_000]
        except Exception:
            return f"[Could not read: {output_path.name}]"
    return f"[Binary file: {output_path.name} — open externally]"


def _format_meta(manifest: Manifest) -> str:
    """Build a short metadata string for display."""
    parts = []
    if manifest.duration_seconds is not None:
        parts.append(f"Duration: {manifest.duration_seconds:.1f}s")
    if manifest.provider:
        parts.append(f"Provider: {manifest.provider}")
    return " · ".join(parts)
