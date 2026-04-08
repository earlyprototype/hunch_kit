"""MCP Resources — read-only data for LLM context.

Resources provide experiment data, lineage information, and rubric
definitions without side effects.
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from ._config import _experiments_dir, _rubrics_dir


def register_resources(mcp: FastMCP) -> None:
    """Register all hunch_kit resources with the MCP server."""

    @mcp.resource("experiment://list")
    def experiment_list() -> str:
        """List all experiments with summary information.

        Returns a JSON array of experiment summaries including
        ID, status, baseline, hypothesis, and scoring state.
        """
        from hunch_kit.manifest import load_all_manifests

        manifests = load_all_manifests(_experiments_dir())
        summaries = []
        for m in manifests:
            summaries.append({
                "id": m.id,
                "status": m.status,
                "baseline": m.baseline,
                "hypothesis": m.hypothesis,
                "variable_changed": m.variable_changed,
                "overall_preference": m.overall_preference,
                "has_human_scores": bool(m.human_scores),
                "has_automated_scores": bool(m.automated_scores),
            })
        return json.dumps(summaries, indent=2)

    @mcp.resource("experiment://lineage")
    def experiment_lineage() -> str:
        """Full experiment genealogy tree.

        Returns a JSON object mapping each experiment ID to its
        list of child experiment IDs.
        """
        from hunch_kit.manifest import load_all_manifests, build_lineage

        manifests = load_all_manifests(_experiments_dir())
        tree = build_lineage(manifests)
        return json.dumps(tree, indent=2)

    @mcp.resource("experiment://current/{experiment_id}")
    def experiment_current(experiment_id: str) -> str:
        """Full manifest for a specific experiment.

        Returns the complete experiment.yaml content as JSON.
        """
        from hunch_kit.manifest import Manifest

        exp_dir = _experiments_dir() / experiment_id
        if not exp_dir.exists():
            return json.dumps({"error": f"Experiment not found: {experiment_id}"})

        manifest = Manifest.load(exp_dir)
        return json.dumps(manifest.to_dict(), indent=2)

    @mcp.resource("rubric://list")
    def rubric_list() -> str:
        """List available evaluation rubrics.

        Returns a JSON array of rubric summaries including
        name, description, and dimension count.
        """
        from hunch_kit.rubric import find_rubrics, Rubric

        rubric_dir = _rubrics_dir()
        paths = find_rubrics(rubric_dir)
        summaries = []
        for p in paths:
            try:
                r = Rubric.load(p)
                summaries.append({
                    "name": r.name,
                    "description": r.description,
                    "dimensions": len(r.dimensions),
                    "file": p.name,
                })
            except Exception as e:
                summaries.append({
                    "file": p.name,
                    "error": str(e),
                })
        return json.dumps(summaries, indent=2)

    @mcp.resource("rubric://{name}")
    def rubric_detail(name: str) -> str:
        """Full rubric definition by name.

        Returns the complete rubric YAML content as JSON,
        including all dimensions, scales, and anchors.
        """
        from hunch_kit.rubric import load_rubric_by_name

        rubric = load_rubric_by_name(_rubrics_dir(), name)
        if rubric is None:
            return json.dumps({"error": f"Rubric not found: {name}"})
        return json.dumps(rubric.to_dict(), indent=2)
