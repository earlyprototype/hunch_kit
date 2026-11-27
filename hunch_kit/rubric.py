"""Evaluation rubric schema, validation, and YAML I/O.

Rubrics define the scoring dimensions for experiment evaluation.
Each dimension declares who evaluates it (human, LLM, or both) and
provides optional score anchors for calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import re
from typing import Any, TextIO

import yaml

from ._local_files import directory_entries, local_directory, local_text_file

RUBRIC_DIR_NAME = "rubrics"


@dataclass
class Dimension:
    """A single evaluation dimension within a rubric."""

    name: str
    description: str = ""
    scale_min: int = 1
    scale_max: int = 10
    evaluator: str = "human"  # "human" | "llm" | "both"
    anchors: dict[int | str, str] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append("Dimension missing required field: name")
        if self.evaluator not in ("human", "llm", "both"):
            errors.append(
                f"Dimension '{self.name}': evaluator must be "
                f"'human', 'llm', or 'both' (got {self.evaluator!r})"
            )
        if self.scale_min >= self.scale_max:
            errors.append(
                f"Dimension '{self.name}': scale_min must be less than scale_max"
            )
        return errors


@dataclass
class Rubric:
    """A collection of evaluation dimensions for a domain."""

    name: str = ""
    description: str = ""
    dimensions: list[Dimension] = field(default_factory=list)

    # -- Validation -----------------------------------------------------------

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append("Rubric missing required field: name")
        if not self.dimensions:
            errors.append("Rubric must have at least one dimension")
        names_seen: set[str] = set()
        for dim in self.dimensions:
            errors.extend(dim.validate())
            if dim.name in names_seen:
                errors.append(f"Duplicate dimension name: {dim.name!r}")
            names_seen.add(dim.name)
        return errors

    # -- Serialisation --------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, directory: Path) -> Path:
        """Write the rubric to *directory*/<name>.yaml."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        filename = self.name.lower().replace(" ", "_") + ".yaml"
        path = directory / filename
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

    # -- Deserialisation ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Rubric:
        with Path(path).open(encoding="utf-8") as stream:
            return cls.from_stream(stream)

    @classmethod
    def from_stream(cls, stream: TextIO) -> Rubric:
        return cls._from_dict(yaml.safe_load(stream) or {})

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Rubric:
        if not isinstance(data, dict):
            raise ValueError("Expected a rubric mapping")
        dims_raw = data.get("dimensions", [])
        if not isinstance(dims_raw, list):
            raise ValueError("Expected a list of rubric dimensions")
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            dimensions=[Dimension(**d) for d in dims_raw],
        )

    # -- Queries --------------------------------------------------------------

    def human_dimensions(self) -> list[Dimension]:
        """Dimensions that require human scoring."""
        return [d for d in self.dimensions if d.evaluator in ("human", "both")]

    def llm_dimensions(self) -> list[Dimension]:
        """Dimensions that support LLM scoring."""
        return [d for d in self.dimensions if d.evaluator in ("llm", "both")]


# -- Discovery ---------------------------------------------------------------


def find_rubrics(rubric_dir: Path) -> list[Path]:
    """Find all rubric YAML files in the given directory."""
    rubric_dir = Path(rubric_dir)
    if not rubric_dir.is_dir():
        return []
    return sorted(
        p for p in rubric_dir.glob("*.yaml")
        if not p.name.startswith("_")
    )


def _read_candidate(campaign: Path, path: Path, local_only: bool) -> Rubric | None:
    try:
        if not local_only:
            path = path.resolve(strict=True)
        with local_directory(campaign, path.parent) as fd:
            if fd is None:
                return None
            with local_text_file(path, fd) as stream:
                if stream is None:
                    return None
                rubric = Rubric.from_stream(stream)
    except (OSError, RuntimeError, yaml.YAMLError, TypeError, ValueError):
        return None
    if not isinstance(rubric.name, str) or not rubric.name.strip() or not rubric.dimensions:
        return None
    if any(not isinstance(d.name, str) or not d.name.strip() for d in rubric.dimensions):
        return None
    return rubric


def _candidate_paths(directory: Path, fd: int, local_only: bool) -> list[Path]:
    paths = []
    for entry in directory_entries(directory, fd):
        path = directory / entry.name
        if not path.match("*.yaml"):
            continue
        try:
            if not entry.is_file(follow_symlinks=False) and (local_only or not entry.is_symlink()):
                continue
        except OSError:
            # Retain the filename's precedence. Opening it will verify its type.
            pass
        paths.append(path)
    return paths


def load_rubric_by_name(rubric_dir: Path, name: str, *, local_only: bool = False) -> Rubric | None:
    """Prefer a filename or stem over a unique declared name.

    Evaluation accepts links to verified targets inside the campaign.
    A local-only lookup excludes links throughout the rubric path.
    """
    if not isinstance(name, str) or not name.strip() or name in (".", ".."):
        return None
    if re.search(r'^[A-Za-z]:|[\\/\x00-\x1f]', name):
        return None
    directory = Path(rubric_dir).absolute()
    campaign = directory.parent
    try:
        if not local_only:
            campaign = campaign.resolve(strict=True)
            directory = directory.resolve(strict=True)
        with local_directory(campaign, directory) as fd:
            if fd is None:
                return None
            candidates = _candidate_paths(directory, fd, local_only)
    except (OSError, RuntimeError):
        return None

    key = name.lower()
    filename = name if key.endswith(".yaml") else name + ".yaml"
    alias = key.replace(" ", "_")
    matches = (
        [p for p in candidates if p.name == filename]
        or [p for p in candidates if p.stem == name]
        or [p for p in candidates if p.name.lower() == key or p.stem.lower() == key]
        or [p for p in candidates if p.stem.lower() == alias]
    )
    if matches:
        return _read_candidate(campaign, matches[0], local_only) if len(matches) == 1 else None

    declared = None
    for path in candidates:
        rubric = _read_candidate(campaign, path, local_only)
        if rubric is not None and rubric.name.lower() == key:
            if declared is not None:
                return None
            declared = rubric
    return declared
