"""Markdown export of human scores against each experiment's baseline."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .manifest import Manifest, build_lineage
from .rubric import Dimension, load_rubric_by_name


def _code(value: object) -> str:
    """Keep manifest text literal and on one Markdown line."""
    if value is None or value == "":
        return "Missing"
    # JSON escapes distinguish control characters from literal backslashes.
    text = re.sub(r"\\|[^\S ]", lambda m: json.dumps(m[0])[1:-1], str(value))
    text = re.sub(r"^ +| +$| {2,}", lambda m: r"\u0020" * len(m[0]), text)
    runs = re.findall(r"`+", text)
    if runs:
        fence = "`" * (max(map(len, runs)) + 1)
        return f"{fence} {text} {fence}"
    return f"`{text}`"


def _score_text(value: object) -> str:
    if value is None:
        return "Missing"
    if type(value) in (float, int):
        return str(value)
    return _code(value)


def _anchor_labels(dim: Dimension) -> dict[int, str] | None:
    """Index labels by integer position. An ambiguous or invalid map has no scale."""
    if dim.anchors is None:
        return {}
    if not isinstance(dim.anchors, dict):
        return None
    try:
        labels = {int(str(position)): label for position, label in dim.anchors.items()}
    except ValueError:
        return None
    return labels if len(labels) == len(dim.anchors) else None


def _change(
    before: object, after: object, parent_dim: Dimension | None, dim: Dimension | None,
) -> str:
    if parent_dim is None or dim is None:
        return "Incomparable rubrics"
    parent_labels = _anchor_labels(parent_dim)
    if (
        parent_dim.description != dim.description
        or parent_dim.scale_min != dim.scale_min
        or parent_dim.scale_max != dim.scale_max
        or parent_labels is None
        or parent_labels != _anchor_labels(dim)
    ):
        return "Incomparable rubrics"
    if before is None or after is None:
        return "Missing score"
    try:
        old, new = Decimal(str(before)), Decimal(str(after))
    except InvalidOperation:
        return "Non-numeric score"
    if not all(n.is_finite() and dim.scale_min <= n <= dim.scale_max for n in (old, new)):
        return "Score outside scale"
    delta = new - old
    return f"{delta.normalize():+f}" if delta else "+0"


def _rubric_name(experiment: Manifest) -> str:
    return experiment.rubric if isinstance(experiment.rubric, str) else ""


def _experiment_section(
    experiment: Manifest, parent: Manifest | None, rubrics: dict[str, dict[str, Dimension]],
) -> list[str]:
    parent_label = _code(experiment.baseline) if experiment.baseline else "Root"
    if experiment.baseline and parent is None:
        parent_label += " (missing manifest)"
    rubric_name = _rubric_name(experiment)
    rubric_label = "Unspecified"
    if experiment.rubric not in (None, ""):
        rubric_label = _code(experiment.rubric)
        if rubric_name not in rubrics:
            rubric_label += " (unavailable)"
    lines = [
        "", f"## {_code(experiment.id)}", "",
        f"Parent: {parent_label}", "",
        f"Changed variable: {_code(experiment.variable_changed)} = {_code(experiment.variable_value)}", "",
        f"Rubric: {rubric_label}", "",
    ]
    dims = rubrics.get(rubric_name, {})
    parent_dims = rubrics.get(_rubric_name(parent), {}) if parent else {}
    scores = experiment.human_scores or {}
    parent_scores = (parent.human_scores or {}) if parent else {}
    names = dims.keys() | parent_dims.keys() | scores.keys() | parent_scores.keys()
    if not names:
        lines.append("No human scores.")
        return lines

    lines.extend(["| Dimension | Parent score | Human score | Change |", "| --- | --- | --- | --- |"])
    for name in sorted(names):
        before, after = parent_scores.get(name), scores.get(name)
        if parent is None:
            change = "Missing parent" if experiment.baseline else "Root"
        else:
            change = _change(before, after, parent_dims.get(name), dims.get(name))
        cells = [_code(name), _score_text(before) if parent else "n/a", _score_text(after), change]
        lines.append("| " + " | ".join(cell.replace("|", r"\|") for cell in cells) + " |")
    return lines


def render_scorecard(manifests: list[Manifest], rubrics_dir: Path, campaign_name: str) -> str:
    """Render roots first, followed by children grouped by their recorded parent."""
    title = "# Campaign scorecard"
    if campaign_name:
        title += f": {_code(campaign_name)}"
    if not manifests:
        return f"{title}\n\nNo experiments found."

    by_id = {}
    for manifest in manifests:
        if not isinstance(manifest.id, str) or not manifest.id.strip():
            raise ValueError("Each experiment manifest must have a nonempty string ID.")
        if manifest.id in by_id:
            raise ValueError(f"Duplicate experiment ID {manifest.id!r}.")
        by_id[manifest.id] = manifest

    lines = [title]
    rubrics = {}
    for name in sorted({_rubric_name(m) for m in manifests} - {""}):
        rubric = load_rubric_by_name(rubrics_dir, name)
        if rubric is not None:
            rubrics[name] = {d.name: d for d in rubric.human_dimensions()}

    for root in sorted((m for m in manifests if not m.baseline), key=lambda m: m.id):
        lines.extend(_experiment_section(root, None, rubrics))
    for parent_id, children in sorted(build_lineage(manifests).items()):
        for child_id in sorted(children):
            lines.extend(_experiment_section(by_id[child_id], by_id.get(parent_id), rubrics))
    return "\n".join(lines)
