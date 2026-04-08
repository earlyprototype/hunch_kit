"""Evaluation rubric schema, validation, and YAML I/O.

Rubrics define the scoring dimensions for experiment evaluation.
Each dimension declares who evaluates it (human, LLM, or both) and
provides optional score anchors for calibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
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
    anchors: dict[int, str] = field(default_factory=dict)

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


def load_rubric_by_name(rubric_dir: Path, name: str) -> Rubric | None:
    """Load a rubric by name from the rubrics directory."""
    rubric_dir = Path(rubric_dir)
    filename = name.lower().replace(" ", "_") + ".yaml"
    path = rubric_dir / filename
    if path.exists():
        return Rubric.load(path)
    for p in rubric_dir.glob("*.yaml"):
        r = Rubric.load(p)
        if r.name.lower() == name.lower():
            return r
    return None
