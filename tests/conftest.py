"""Shared test fixtures."""

from pathlib import Path

import pytest

from hunch_kit import manifest


@pytest.fixture(autouse=True)
def isolated_lock_directory(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(manifest, "LOCK_DIRECTORY", tmp_path_factory.getbasetemp() / "user-locks")


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary hunch_kit project structure."""
    (tmp_path / "experiments").mkdir()
    (tmp_path / "rubrics").mkdir()
    return tmp_path
