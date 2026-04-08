"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary hunch_kit project structure."""
    (tmp_path / "experiments").mkdir()
    (tmp_path / "rubrics").mkdir()
    return tmp_path
