"""Evaluation rubric schema, validation, and YAML I/O.

Rubrics define the scoring dimensions for experiment evaluation.
Each dimension declares who evaluates it (human, LLM, or both) and
provides optional score anchors for calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import re
import stat
from typing import Any

import yaml


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
        path = Path(path)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> Rubric:
        dims_raw = data.pop("dimensions", [])
        dimensions = [
            Dimension(**d) if isinstance(d, dict) else d for d in dims_raw
        ]
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            dimensions=dimensions,
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


def _local_mode(path: Path) -> int:
    """Inspect the directory entry without following links or Windows junctions."""
    try:
        entry = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return 0
    if getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return 0
    return entry.st_mode


def load_rubric_by_name(rubric_dir: Path, name: str) -> Rubric | None:
    """Match a rubric name against regular YAML files in the local directory."""
    if not isinstance(name, str) or not name.strip() or name in (".", ".."):
        return None
    if re.search(r'[\\/:<>"|?*\x00-\x1f]', name):
        return None
    rubric_dir = Path(rubric_dir)
    if not stat.S_ISDIR(_local_mode(rubric_dir)):
        return None

    preferred = rubric_dir / (name.lower().replace(" ", "_") + ".yaml")
    # Check the conventional filename first, then match declared names.
    candidates = sorted(rubric_dir.glob("*.yaml"), key=lambda path: (path != preferred, path))
    for path in candidates:
        if not stat.S_ISREG(_local_mode(path)):
            continue
        rubric = Rubric.load(path)
        if path == preferred or rubric.name.lower() == name.lower():
            return rubric
    return None
