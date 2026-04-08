"""Tests for runner error handling and edge cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from hunch_kit.manifest import Manifest
from hunch_kit.providers.base import BaseProvider, ProviderResult
from hunch_kit.runner import run_experiment


class CrashingProvider(BaseProvider):
    """A provider that always raises."""

    name = "crasher"

    def run(self, input_text: str, config: dict[str, Any] | None = None) -> ProviderResult:
        raise RuntimeError("Provider exploded!")


class SlowSuccessProvider(BaseProvider):
    """A provider that succeeds with custom output."""

    name = "slow_success"

    def run(self, input_text: str, config: dict[str, Any] | None = None) -> ProviderResult:
        return ProviderResult(
            output="processed: " + input_text,
            status="success",
            metadata={"engine": "test"},
        )


@pytest.fixture
def experiment_dir(tmp_path: Path) -> Path:
    """Create a minimal experiment directory with echo provider."""
    exp = tmp_path / "experiments" / "ex_test"
    m = Manifest(
        id="ex_test", hypothesis="hypothesis",
        variable_changed="var", variable_value="val",
        provider="echo", input_path="input.txt",
    )
    m.save(exp)
    (exp / "output").mkdir()
    (exp / "input.txt").write_text("hello", encoding="utf-8")
    return exp


class TestProviderCrash:
    def test_provider_crash_sets_failed_status(self, experiment_dir: Path) -> None:
        """A provider that raises should result in status='failed', not an unhandled exception."""
        with patch("hunch_kit.runner.resolve_provider", return_value=CrashingProvider()):
            result = run_experiment(experiment_dir)

        assert result.status == "failed"
        assert "exploded" in result.error.lower()

        # Manifest should be updated with failed status
        m = Manifest.load(experiment_dir)
        assert m.status == "failed"

    def test_provider_crash_preserves_manifest(self, experiment_dir: Path) -> None:
        """A failed run should not corrupt the manifest."""
        original = Manifest.load(experiment_dir)

        with patch("hunch_kit.runner.resolve_provider", return_value=CrashingProvider()):
            run_experiment(experiment_dir)

        m = Manifest.load(experiment_dir)
        assert m.id == original.id
        assert m.hypothesis == original.hypothesis


class TestMissingInput:
    def test_missing_input_file_fails_gracefully(self, tmp_path: Path) -> None:
        """An experiment whose input_path doesn't exist should fail gracefully."""
        exp = tmp_path / "experiments" / "ex_missing"
        m = Manifest(
            id="ex_missing", hypothesis="h",
            variable_changed="v", variable_value="val",
            provider="echo", input_path="nonexistent.txt",
        )
        m.save(exp)
        (exp / "output").mkdir()

        result = run_experiment(exp)
        assert result.status == "failed"
        assert "nonexistent.txt" in result.error

        # Manifest should reflect the failure
        m = Manifest.load(exp)
        assert m.status == "failed"


class TestProviderOverride:
    def test_override_replaces_manifest_provider(self, experiment_dir: Path) -> None:
        """The provider_override parameter should take precedence."""
        with patch("hunch_kit.runner.resolve_provider", return_value=SlowSuccessProvider()):
            result = run_experiment(experiment_dir, provider_override="slow_success")

        assert result.status == "success"
        assert "processed" in result.output


class TestSuccessfulRun:
    def test_populates_duration(self, experiment_dir: Path) -> None:
        """A successful run should have a non-negative duration."""
        result = run_experiment(experiment_dir)
        assert result.status == "success"
        assert result.duration_seconds >= 0.0

    def test_output_is_non_empty(self, experiment_dir: Path) -> None:
        """A successful echo run should produce output."""
        result = run_experiment(experiment_dir)
        assert result.status == "success"
        assert len(result.output) > 0
