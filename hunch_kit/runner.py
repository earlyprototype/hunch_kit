"""Experiment execution orchestrator.

Loads a manifest, resolves the provider, executes the experiment,
and writes results back to the manifest.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .manifest import Manifest
from .project import ProjectConfig, infer_campaign_root_from_experiment_dir, providers_dir
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


def discover_local_provider_classes(
    campaign_root: Path | None,
) -> dict[str, type[BaseProvider]]:
    """Load ``BaseProvider`` subclasses from the campaign ``providers`` folder."""
    out: dict[str, type[BaseProvider]] = {}
    if campaign_root is None:
        return out
    campaign_root = Path(campaign_root)
    config = ProjectConfig.load(campaign_root)
    pdir = providers_dir(campaign_root, config)
    if not pdir.is_dir():
        return out

    for py in sorted(pdir.glob("*.py")):
        if py.name.startswith("_") or py.name == "__init__.py":
            continue
        mod_name = f"hunch_kit_campaign_provider_{py.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, py)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            logger.warning("Failed to load provider module %s", py, exc_info=True)
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj is BaseProvider or not issubclass(obj, BaseProvider):
                continue
            pname = getattr(obj, "name", None)
            if pname:
                out[pname] = obj
    return out


def resolve_provider(
    name: str,
    *,
    campaign_root: Path | None = None,
) -> BaseProvider:
    """Look up a provider by name; local campaign providers override built-ins."""
    _ensure_builtins_registered()
    local = discover_local_provider_classes(campaign_root)
    cls = local.get(name) or PROVIDER_REGISTRY.get(name)
    if cls is None:
        available = sorted({*local.keys(), *PROVIDER_REGISTRY.keys()})
        listed = ", ".join(available) if available else "(none)"
        raise ValueError(f"Unknown provider {name!r}. Available: {listed}")
    if name in local:
        logger.debug("Resolved local provider %r from campaign", name)
    else:
        logger.debug("Resolved built-in provider %r", name)
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
    logger.info("Running experiment %r", manifest.id)

    provider_name = provider_override or manifest.provider
    if not provider_name:
        raise ValueError(
            f"Experiment {manifest.id!r} has no provider set and "
            f"no override was given."
        )

    campaign_root = infer_campaign_root_from_experiment_dir(experiment_dir)
    provider = resolve_provider(provider_name, campaign_root=campaign_root)

    manifest.status = "running"
    manifest.save(experiment_dir)

    start = time.monotonic()
    try:
        input_text = _read_input(experiment_dir, manifest)
        result = provider.run(input_text, manifest.provider_config or None)
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.error(
            "Experiment %r failed after %.1fs: %s",
            manifest.id, elapsed, exc,
        )
        result = ProviderResult(
            output="",
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=round(elapsed, 3),
        )

    _apply_result(manifest, result, experiment_dir)
    manifest.save(experiment_dir)
    logger.info(
        "Experiment %r finished: status=%s duration=%.1fs",
        manifest.id, result.status,
        result.duration_seconds or 0,
    )
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

    campaign_root = infer_campaign_root_from_experiment_dir(experiment_dir)
    provider = resolve_provider(provider_name, campaign_root=campaign_root)

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
