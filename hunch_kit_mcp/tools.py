"""MCP Tools — operations with real side effects.

Each tool receives structured inputs, validates them, performs file
operations, and returns confirmation. The LLM's role is conversational
elicitation; the tool's role is construction, validation, and persistence.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ._config import _experiments_dir, _rubrics_dir

logger = logging.getLogger(__name__)


def register_tools(mcp: FastMCP) -> None:
    """Register all hunch_kit tools with the MCP server."""

    @mcp.tool()
    def init_experiment(
        id: str,
        hypothesis: str,
        variable_changed: str,
        variable_value: str,
        baseline: str = "",
        provider: str = "echo",
        rubric: str = "",
        input_path: str = "",
    ) -> str:
        """Create a new experiment directory and manifest.

        Args:
            id: Unique experiment identifier (e.g. 'ex_001_baseline')
            hypothesis: What you expect this change to achieve
            variable_changed: The single variable being tested
            variable_value: What the variable was changed to
            baseline: Parent experiment ID (empty for the first experiment)
            provider: Provider to use for execution (default: echo)
            rubric: Rubric name for evaluation (optional)
            input_path: Path to input file relative to experiment dir (optional)

        Returns:
            Confirmation message with the created file paths.
        """
        from hunch_kit.manifest import Manifest

        exp_dir = _experiments_dir() / id
        if exp_dir.exists():
            return f"Error: Experiment directory already exists: {exp_dir}"

        manifest = Manifest.create(
            experiment_id=id,
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
            return "Validation failed:\n  " + "\n  ".join(errors)

        exp_dir.mkdir(parents=True)
        (exp_dir / "output").mkdir()
        manifest.save(exp_dir)

        if baseline:
            parent_dir = _experiments_dir() / baseline
            if parent_dir.exists():
                try:
                    parent = Manifest.load(parent_dir)
                    if id not in parent.children:
                        parent.children.append(id)
                        parent.save(parent_dir)
                except Exception as exc:
                    logger.warning(
                        "Could not update parent lineage for %s: %s",
                        baseline, exc,
                    )

        return (
            f"Experiment created: {id}\n"
            f"Directory: {exp_dir}\n"
            f"Manifest: {exp_dir / 'experiment.yaml'}"
        )

    @mcp.tool()
    def create_rubric(
        name: str,
        description: str,
        dimensions: list[dict[str, Any]],
    ) -> str:
        """Create a new evaluation rubric from structured dimensions.

        Args:
            name: Rubric name (e.g. 'slide_quality', 'content_accuracy')
            description: What this rubric evaluates
            dimensions: List of dimension objects, each with:
                - name (str): Dimension identifier
                - description (str): What this dimension measures
                - scale_min (int): Minimum score (default 1)
                - scale_max (int): Maximum score (default 10)
                - evaluator (str): 'human', 'llm', or 'both'
                - anchors (dict): Optional score-to-description mapping

        Returns:
            Confirmation with the rubric file path.
        """
        from hunch_kit.rubric import Rubric, Dimension

        dims = []
        for d in dimensions:
            dims.append(Dimension(
                name=d.get("name", ""),
                description=d.get("description", ""),
                scale_min=d.get("scale_min", 1),
                scale_max=d.get("scale_max", 10),
                evaluator=d.get("evaluator", "human"),
                anchors=d.get("anchors", {}),
            ))

        rubric = Rubric(name=name, description=description, dimensions=dims)

        errors = rubric.validate()
        if errors:
            return "Validation failed:\n  " + "\n  ".join(errors)

        path = rubric.save(_rubrics_dir())
        return f"Rubric created: {name}\nFile: {path}"

    @mcp.tool()
    def run_experiment(
        experiment_id: str,
        provider: str = "",
    ) -> str:
        """Execute an experiment through its configured provider.

        Args:
            experiment_id: ID of the experiment to run
            provider: Optional provider override

        Returns:
            Execution result summary.
        """
        from hunch_kit.runner import run_experiment as _run

        exp_dir = _experiments_dir() / experiment_id
        if not exp_dir.exists():
            return f"Error: Experiment not found: {experiment_id}"

        result = _run(exp_dir, provider_override=provider or None)

        return (
            f"Experiment: {experiment_id}\n"
            f"Status: {result.status}\n"
            f"Duration: {result.duration_seconds:.1f}s\n"
            f"Output length: {len(result.output)} chars"
            + (f"\nError: {result.error}" if result.error else "")
        )

    @mcp.tool()
    def score_experiment(
        experiment_id: str,
        scores: dict[str, float],
        overall_preference: str = "",
        notes: str = "",
    ) -> str:
        """Record human evaluation scores for an experiment.

        Args:
            experiment_id: ID of the experiment to score
            scores: Dict mapping dimension names to numeric scores
            overall_preference: 'better', 'worse', or 'equivalent' vs baseline
            notes: Optional free-form observations

        Returns:
            Confirmation of saved scores.
        """
        from hunch_kit.manifest import Manifest

        exp_dir = _experiments_dir() / experiment_id
        if not exp_dir.exists():
            return f"Error: Experiment not found: {experiment_id}"

        manifest = Manifest.load(exp_dir)

        for key, value in scores.items():
            manifest.human_scores[key] = value

        if overall_preference:
            if overall_preference not in ("better", "worse", "equivalent"):
                return f"Error: overall_preference must be 'better', 'worse', or 'equivalent'"
            manifest.overall_preference = overall_preference

        if notes:
            manifest.notes = notes

        manifest.save(exp_dir)

        return (
            f"Scores saved for: {experiment_id}\n"
            f"Dimensions scored: {', '.join(scores.keys())}\n"
            f"Preference: {overall_preference or '(not set)'}"
        )

    @mcp.tool()
    def update_lineage(
        experiment_id: str,
        child_id: str,
    ) -> str:
        """Add a child experiment to a parent's lineage.

        Args:
            experiment_id: Parent experiment ID
            child_id: Child experiment ID to add

        Returns:
            Confirmation message.
        """
        from hunch_kit.manifest import Manifest

        exp_dir = _experiments_dir() / experiment_id
        if not exp_dir.exists():
            return f"Error: Parent experiment not found: {experiment_id}"

        manifest = Manifest.load(exp_dir)
        if child_id not in manifest.children:
            manifest.children.append(child_id)
            manifest.save(exp_dir)
            return f"Added {child_id} as child of {experiment_id}"
        return f"{child_id} is already a child of {experiment_id}"
