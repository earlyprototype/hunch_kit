"""Experiment manifest schema, validation, and YAML I/O.

The manifest (experiment.yaml) is the single source of truth for each
experiment: hypothesis, variables, lineage, provider metadata, and scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
import logging
from pathlib import Path
from typing import Any, TextIO

import yaml

from ._local_files import local_files

logger = logging.getLogger(__name__)


SCHEMA_VERSION = "1"
MANIFEST_FILENAME = "experiment.yaml"

REQUIRED_FIELDS = ("id", "hypothesis", "variable_changed", "variable_value")


@dataclass
class Manifest:
    """Experiment manifest — one per experiment directory."""

    # Identity
    id: str = ""
    date: str = ""
    schema_version: str = SCHEMA_VERSION

    # Hypothesis and variables
    hypothesis: str = ""
    variable_changed: str = ""
    variable_value: str = ""

    # Lineage
    baseline: str = ""
    children: list[str] = field(default_factory=list)

    # Provider
    provider: str = ""
    input_path: str = ""
    provider_config: dict[str, Any] = field(default_factory=dict)
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    # Execution
    status: str = "pending"
    output_path: str = ""
    duration_seconds: float | None = None

    # Rubric
    rubric: str = ""

    # Scores
    automated_scores: dict[str, Any] = field(default_factory=dict)
    human_scores: dict[str, Any] = field(default_factory=dict)
    overall_preference: str = ""

    notes: str = ""

    # -- Validation ----------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty if valid)."""
        errors: list[str] = []
        for f in REQUIRED_FIELDS:
            if not getattr(self, f, ""):
                errors.append(f"Missing required field: {f}")
        if self.schema_version != SCHEMA_VERSION:
            errors.append(
                f"Schema version mismatch: got {self.schema_version!r}, "
                f"expected {SCHEMA_VERSION!r}"
            )
        return errors

    # -- Serialisation --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict suitable for YAML output."""
        return {k: v for k, v in asdict(self).items() if v or v == 0}

    def save(self, directory: Path) -> Path:
        """Write the manifest to *directory*/experiment.yaml."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MANIFEST_FILENAME
        path.write_text(
            yaml.dump(
                self.to_dict(),
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        logger.debug("Saved manifest %r to %s", self.id, path)
        return path

    # -- Deserialisation ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Manifest:
        """Load a manifest from a YAML file."""
        path = Path(path)
        if path.is_dir():
            path = path / MANIFEST_FILENAME
        with path.open(encoding="utf-8") as stream:
            manifest = cls.from_stream(stream)
        logger.debug("Loaded manifest %r from %s", manifest.id, path)
        return manifest

    @classmethod
    def from_stream(cls, stream: TextIO) -> Manifest:
        return cls._from_dict(yaml.safe_load(stream) or {})

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Manifest:
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    # -- Factory --------------------------------------------------------------

    @classmethod
    def create(
        cls,
        experiment_id: str,
        hypothesis: str,
        variable_changed: str,
        variable_value: str,
        *,
        baseline: str = "",
        provider: str = "",
        input_path: str = "",
        rubric: str = "",
        provider_config: dict[str, Any] | None = None,
    ) -> Manifest:
        """Create a new manifest with sensible defaults."""
        return cls(
            id=experiment_id,
            date=date.today().isoformat(),
            hypothesis=hypothesis,
            variable_changed=variable_changed,
            variable_value=variable_value,
            baseline=baseline,
            provider=provider,
            input_path=input_path,
            rubric=rubric,
            provider_config=provider_config or {},
        )


# -- Discovery ---------------------------------------------------------------


def find_experiments(root: Path) -> list[Path]:
    """Find all experiment directories under *root* that contain a manifest."""
    root = Path(root)
    campaign = root.parent.resolve()
    if not root.resolve().is_relative_to(campaign) or not root.is_dir():
        return []
    return sorted(
        path.parent for path in root.rglob(MANIFEST_FILENAME)
        if path.resolve().is_relative_to(campaign) and path.is_file()
    )


def load_all_manifests(root: Path, *, local_only: bool = False) -> list[Manifest]:
    """Load campaign manifests. Read only verified, unlinked files for an export."""
    if not local_only:
        return [Manifest.load(directory) for directory in find_experiments(root)]
    manifests = {
        path.parent: Manifest.from_stream(stream)
        for path, stream in local_files(root, MANIFEST_FILENAME, recursive=True)
    }
    return [manifests[directory] for directory in sorted(manifests)]


def build_lineage(manifests: list[Manifest]) -> dict[str, list[str]]:
    """Build a parent -> children mapping from a list of manifests."""
    tree: dict[str, list[str]] = {}
    for m in manifests:
        tree.setdefault(m.id, [])
        if m.baseline:
            tree.setdefault(m.baseline, []).append(m.id)
    return tree
