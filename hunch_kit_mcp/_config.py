"""Shared workspace resolution for the MCP server modules."""

from __future__ import annotations

import os
from pathlib import Path


def _workspace() -> Path:
    """Resolve the campaign root (directory with hunch_project.yaml)."""
    env = os.getenv("HUNCH_KIT_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()
    from hunch_kit.project import resolve_campaign_root

    return resolve_campaign_root(explicit=None, start=Path.cwd())


def _experiments_dir() -> Path:
    return _workspace() / "experiments"


def _rubrics_dir() -> Path:
    return _workspace() / "rubrics"
