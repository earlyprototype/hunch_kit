"""Experiment execution orchestrator.

Loads a manifest, resolves the provider, executes the experiment,
and writes results back to the manifest.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from .manifest import Manifest, MANIFEST_FILENAME
from .providers.base import BaseProvider, ProviderResult


PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {}


def register_provider(provider_cls: type[BaseProvider]) -> type[BaseProvider]:
    """Register a provider class by its ``name`` attribute."""
    PROVIDER_REGISTRY[provider_cls.name] = provider_cls
    return provider_cls


def _ensure_builtins_registered() -> None:
    """Lazily register built-in providers on first use."""
    if "echo" not in PROVIDER_REGISTRY:
        from .providers.echo import EchoProvider
        register_provider(EchoProvider)


def resolve_provider(name: str) -> BaseProvider:
    """Look up a provider by name and return an instance."""
    _ensure_builtins_registered()
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(PROVIDER_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unknown provider {name!r}. Available: {available}"
        )
    return cls()


def run_experiment(
    experiment_dir: Path,
    *,
    provider_override: str | None = None,
) -> ProviderResult:
    """Execute an experiment and update its manifest.

    Args:
        experiment_dir: Path to the experiment directory containing
            an ``experiment.yaml`` manifest.
        provider_override: If set, use this provider instead of the
            one declared in the manifest.

    Returns:
        The ``ProviderResult`` from the provider execution.
    """
    experiment_dir = Path(experiment_dir)
    manifest = Manifest.load(experiment_dir)

    provider_name = provider_override or manifest.provider
    if not provider_name:
        raise ValueError(
            f"Experiment {manifest.id!r} has no provider set and "
            f"no override was given."
        )

    provider = resolve_provider(provider_name)

    input_text = _read_input(experiment_dir, manifest)

    manifest.status = "running"
    manifest.save(experiment_dir)

    start = time.monotonic()
    try:
        result = provider.run(input_text, manifest.provider_config or None)
    except Exception as exc:
        elapsed = time.monotonic() - start
        result = ProviderResult(
            output="",
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=round(elapsed, 3),
        )

    _apply_result(manifest, result, experiment_dir)
    manifest.save(experiment_dir)
    return result


async def run_experiment_async(
    experiment_dir: Path,
    *,
    provider_override: str | None = None,
) -> ProviderResult:
    """Async variant of ``run_experiment``."""
    experiment_dir = Path(experiment_dir)
    manifest = Manifest.load(experiment_dir)

    provider_name = provider_override or manifest.provider
    if not provider_name:
        raise ValueError(
            f"Experiment {manifest.id!r} has no provider set and "
            f"no override was given."
        )

    provider = resolve_provider(provider_name)
    input_text = _read_input(experiment_dir, manifest)

    manifest.status = "running"
    manifest.save(experiment_dir)

    start = time.monotonic()
    try:
        result = await provider.async_run(input_text, manifest.provider_config or None)
    except Exception as exc:
        elapsed = time.monotonic() - start
        result = ProviderResult(
            output="",
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=round(elapsed, 3),
        )

    _apply_result(manifest, result, experiment_dir)
    manifest.save(experiment_dir)
    return result


def _read_input(experiment_dir: Path, manifest: Manifest) -> str:
    """Resolve and read the experiment input."""
    if not manifest.input_path:
        return ""

    input_path = Path(manifest.input_path)
    if not input_path.is_absolute():
        input_path = experiment_dir / input_path

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {input_path} "
            f"(experiment {manifest.id!r})"
        )
    return input_path.read_text(encoding="utf-8")


def _apply_result(
    manifest: Manifest,
    result: ProviderResult,
    experiment_dir: Path,
) -> None:
    """Write provider results back into the manifest."""
    manifest.status = result.status
    manifest.duration_seconds = result.duration_seconds
    manifest.provider_metadata.update(result.metadata)

    if result.error:
        manifest.notes = (
            f"{manifest.notes}\n\nProvider error: {result.error}".strip()
        )

    if result.output:
        output_dir = experiment_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "result.txt"
        output_file.write_text(result.output, encoding="utf-8")
        manifest.output_path = str(output_file.relative_to(experiment_dir))
