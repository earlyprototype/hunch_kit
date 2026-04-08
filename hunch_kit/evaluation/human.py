"""Human evaluation web UI — side-by-side comparison and scoring.

A lightweight FastAPI application that serves a local web interface
for comparing experiment outputs and recording human scores against
rubric dimensions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..manifest import Manifest, find_experiments, load_all_manifests
from ..rubric import Rubric, load_rubric_by_name


STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(
    experiments_dir: Path,
    rubrics_dir: Path,
    filter_id: str | None = None,
) -> FastAPI:
    """Build the FastAPI application for human evaluation."""

    app = FastAPI(title="hunch_kit evaluation")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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

        rows = ""
        for m in manifests:
            status_class = {
                "success": "status-success",
                "failed": "status-failed",
                "pending": "status-pending",
                "running": "status-running",
            }.get(m.status, "")
            scored = "Yes" if m.human_scores else "—"
            rows += (
                f'<tr>'
                f'<td><a href="/eval/{m.id}">{m.id}</a></td>'
                f'<td class="{status_class}">{m.status}</td>'
                f'<td>{m.baseline or "—"}</td>'
                f'<td>{m.overall_preference or "—"}</td>'
                f'<td>{scored}</td>'
                f'</tr>\n'
            )

        html = _page(
            "Experiments",
            f"""
            <h1>hunch_kit — Experiments</h1>
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Status</th><th>Baseline</th>
                  <th>Preference</th><th>Scored</th>
                </tr>
              </thead>
              <tbody>{rows}</tbody>
            </table>
            """,
        )
        return HTMLResponse(html)

    @app.get("/eval/{experiment_id}", response_class=HTMLResponse)
    async def eval_page(experiment_id: str) -> HTMLResponse:
        exp_dir = app.state.experiments_dir / experiment_id
        if not exp_dir.exists():
            return HTMLResponse("<h1>Experiment not found</h1>", status_code=404)

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
                baseline_meta = _render_meta(baseline_manifest)

        scoring_form = _build_scoring_form(manifest, rubric)

        html = _page(
            f"Evaluate: {experiment_id}",
            f"""
            <h1>Evaluate: {experiment_id}</h1>
            <div class="meta-bar">
              <span><strong>Hypothesis:</strong> {manifest.hypothesis}</span>
              <span><strong>Variable:</strong> {manifest.variable_changed} = {manifest.variable_value}</span>
              <span><strong>Status:</strong> {manifest.status}</span>
            </div>

            <div class="comparison">
              <div class="panel">
                <h2>Baseline{' — ' + manifest.baseline if manifest.baseline else ' — (none)'}</h2>
                {baseline_meta}
                <div class="output-box">
                  {'<pre>' + _escape(baseline_output) + '</pre>' if baseline_output else '<p class="empty">No baseline output available</p>'}
                </div>
              </div>
              <div class="panel">
                <h2>Current — {experiment_id}</h2>
                {_render_meta(manifest)}
                <div class="output-box">
                  {'<pre>' + _escape(current_output) + '</pre>' if current_output else '<p class="empty">No output yet — run the experiment first</p>'}
                </div>
              </div>
            </div>

            <div class="scoring-section">
              <h2>Scoring</h2>
              <form id="scoring-form" data-experiment-id="{experiment_id}">
                {scoring_form}
                <div class="form-row">
                  <label for="overall_preference">Overall preference vs baseline</label>
                  <select id="overall_preference" name="overall_preference">
                    <option value="">— select —</option>
                    <option value="better"{'selected' if manifest.overall_preference == 'better' else ''}>Better</option>
                    <option value="equivalent"{'selected' if manifest.overall_preference == 'equivalent' else ''}>Equivalent</option>
                    <option value="worse"{'selected' if manifest.overall_preference == 'worse' else ''}>Worse</option>
                  </select>
                </div>
                <div class="form-row">
                  <label for="notes">Notes</label>
                  <textarea id="notes" name="notes" rows="3">{_escape(manifest.notes)}</textarea>
                </div>
                <button type="submit">Save scores</button>
                <span id="save-status"></span>
              </form>
            </div>
            """,
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


def _render_meta(manifest: Manifest) -> str:
    parts = []
    if manifest.duration_seconds is not None:
        parts.append(f"Duration: {manifest.duration_seconds:.1f}s")
    if manifest.provider:
        parts.append(f"Provider: {manifest.provider}")
    if not parts:
        return ""
    return '<div class="meta-small">' + " · ".join(parts) + "</div>"


def _build_scoring_form(manifest: Manifest, rubric: Rubric | None) -> str:
    if rubric:
        dims = rubric.human_dimensions()
    else:
        dims = []

    if not dims and not manifest.human_scores:
        return '<p class="empty">No rubric assigned — scores can still be recorded as free-form fields below.</p>'

    rows = ""
    for dim in dims:
        current = manifest.human_scores.get(dim.name, "")
        anchor_text = ""
        if dim.anchors:
            anchor_items = " · ".join(
                f"<strong>{k}</strong>: {v}" for k, v in sorted(dim.anchors.items())
            )
            anchor_text = f'<div class="anchors">{anchor_items}</div>'

        rows += f"""
        <div class="form-row">
          <label for="score_{dim.name}">{dim.name}</label>
          <div class="dim-desc">{dim.description}</div>
          {anchor_text}
          <input type="range" id="score_{dim.name}" name="{dim.name}"
                 min="{dim.scale_min}" max="{dim.scale_max}"
                 value="{current or dim.scale_min}"
                 oninput="document.getElementById('val_{dim.name}').textContent=this.value">
          <span id="val_{dim.name}" class="range-val">{current or dim.scale_min}</span>
        </div>
        """

    for key, val in manifest.human_scores.items():
        if rubric and any(d.name == key for d in dims):
            continue
        rows += f"""
        <div class="form-row">
          <label for="score_{key}">{key}</label>
          <input type="number" id="score_{key}" name="{key}" value="{val}" step="0.1">
        </div>
        """

    return rows


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — hunch_kit</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <nav><a href="/">hunch_kit</a></nav>
  <main>{body}</main>
  <script src="/static/app.js"></script>
</body>
</html>"""
