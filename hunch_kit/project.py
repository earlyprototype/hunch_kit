"""Campaign project configuration and root discovery.

A *campaign* is a directory containing ``hunch_project.yaml`` plus
``experiments/``, ``rubrics/``, and optionally ``providers/``.
The CLI and MCP server resolve the campaign root from ``--root``,
``HUNCH_KIT_WORKSPACE``, or by walking upward from the current
working directory for ``hunch_project.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

PROJECT_FILENAME = "hunch_project.yaml"


@dataclass
class ProjectConfig:
    """Campaign-level defaults stored in ``hunch_project.yaml``."""

    name: str = ""
    description: str = ""
    default_provider: str = "echo"
    default_rubric: str = ""
    providers_path: str = "providers"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v or v == 0}

    def save(self, campaign_root: Path) -> Path:
        campaign_root = Path(campaign_root)
        campaign_root.mkdir(parents=True, exist_ok=True)
        path = campaign_root / PROJECT_FILENAME
        path.write_text(
            yaml.dump(
                self.to_dict(),
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, campaign_root: Path) -> ProjectConfig:
        path = Path(campaign_root) / PROJECT_FILENAME
        if not path.is_file():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upward from *start* (or cwd) until ``hunch_project.yaml`` is found.

    Returns the directory containing the file, or ``None`` if not found.
    """
    current = Path(start or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / PROJECT_FILENAME
        if candidate.is_file():
            return parent
    return None


def resolve_campaign_root(
    *,
    explicit: str | Path | None = None,
    start: Path | None = None,
) -> Path:
    """Resolve the campaign directory.

    1. If *explicit* is set, return it as a resolved path.
    2. Else if ``find_project_root(start or cwd)`` finds a file, return it.
    3. Else return ``Path.cwd().resolve()``.
    """
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    found = find_project_root(start or Path.cwd())
    if found is not None:
        return found
    return Path.cwd().resolve()


def infer_campaign_root_from_experiment_dir(experiment_dir: Path) -> Path:
    """Infer campaign root from an experiment folder (``.../experiments/<id>``)."""
    experiment_dir = Path(experiment_dir).resolve()
    found = find_project_root(experiment_dir)
    if found is not None:
        return found
    parent = experiment_dir.parent
    if parent.name == "experiments":
        return parent.parent
    return parent


def providers_dir(campaign_root: Path, config: ProjectConfig | None = None) -> Path:
    """Absolute path to the local providers package directory."""
    cfg = config if config is not None else ProjectConfig.load(campaign_root)
    return (Path(campaign_root) / cfg.providers_path).resolve()
