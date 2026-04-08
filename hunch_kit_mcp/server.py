"""hunch_kit MCP Server — setup and registration.

Exposes hunch_kit functionality to MCP-compatible AI clients via
tools (real operations), resources (read-only data), and prompts
(workflow guidance).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .tools import register_tools
from .resources import register_resources
from .prompts import register_prompts


def get_workspace() -> Path:
    """Resolve the hunch_kit project root."""
    env = os.getenv("HUNCH_KIT_WORKSPACE")
    if env:
        return Path(env)
    return Path.cwd()


mcp = FastMCP(
    "hunch_kit",
    instructions="""
hunch_kit MCP Server — Structured Experimentation

This server provides tools for managing structured experiments
with hypothesis-driven variable isolation and human-in-the-loop evaluation.

## Capabilities
- Tools: Create experiments, create rubrics, run experiments, record scores
- Resources: View experiment data, lineage trees, rubric definitions
- Prompts: Guided experiment planning, rubric construction, result review

## Typical Workflow
1. Plan: Use the experiment_planning prompt to formulate a hypothesis
2. Create rubric: Use create_rubric tool with structured dimensions
3. Init: Use init_experiment tool to scaffold the experiment
4. Run: Use run_experiment tool to execute through a provider
5. Evaluate: Use score_experiment tool to record human scores
6. Review: Use experiment_review prompt to plan next steps

## Key Concepts
- Every experiment declares a hypothesis and a single variable being changed
- Every experiment declares its parent (baseline) to build a lineage tree
- Rubrics define scoring dimensions with anchors for calibration
- Human scoring is first-class; LLM scoring is optional
""".strip(),
)

register_tools(mcp)
register_resources(mcp)
register_prompts(mcp)
