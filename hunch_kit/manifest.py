"""Experiment manifest schema, validation, and YAML I/O.

The manifest (experiment.yaml) is the single source of truth for each
experiment: hypothesis, variables, lineage, provider metadata, and scores.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field, asdict
from datetime import date
import hashlib
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)


SCHEMA_VERSION = "1"
MANIFEST_FILENAME = "experiment.yaml"

REQUIRED_FIELDS = ("id", "hypothesis", "variable_changed", "variable_value")


def _manifest_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


class ManifestConflict(RuntimeError):
    """The manifest changed since this writer read it."""


@contextmanager
def reserve_outputs(*directories: Path):
    """Exclude runs while a ballot commits, and overlapping runs on the same output."""
    with ExitStack() as locks:
        for directory in sorted(set(directories)):
            try:
                locks.enter_context(FileLock(directory / ".experiment.run.lock", timeout=0))
            except Timeout as exc:
                raise ManifestConflict("An output is in use. Retry when the operation finishes.") from exc
        yield


def _current_revision(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@contextmanager
def _publish_output(path: Path, text: str):
    """Install a complete result and restore its predecessor if the manifest commit fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        new = Path(staging) / "new"
        old = Path(staging) / "old"
        new.write_text(text, encoding="utf-8")
        if path.exists():
            shutil.copy2(path, old)
            shutil.copymode(path, new)
        new.replace(path)
        try:
            yield
        except BaseException:
            if old.exists():
                old.replace(path)
            else:
                path.unlink()
            raise


def _write_manifest(
    path: Path, data: dict[str, Any], expected: bytes | None, *, output_text: str | None = None,
) -> bytes:
    revision = _manifest_yaml(data).encode("utf-8")
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        # Normal file creation applies the caller's umask. The staging directory is private.
        temp_path = Path(staging) / "manifest.tmp"
        temp_path.write_bytes(revision)
        # A separate lock stays valid when the YAML file is atomically replaced.
        with FileLock(path.with_name(".experiment.yaml.lock")):
            if _current_revision(path) != expected:
                raise ManifestConflict("The manifest changed. Reload it and retry.")
            if expected is not None:
                shutil.copymode(path, temp_path)
            output = nullcontext() if output_text is None else _publish_output(
                path.parent / data["output_path"], output_text,
            )
            with output:
                temp_path.replace(path)
    return revision


def save_overall_preference(directory: Path, preference: str, *, revision: bytes | None = None) -> None:
    """Update just the preference in the reviewed revision, including extended manifests."""
    path = directory / MANIFEST_FILENAME
    if revision is None:
        revision = path.read_bytes()
    data = yaml.safe_load(revision)
    data["overall_preference"] = preference
    _write_manifest(path, data, revision)


def mark_run_failed(directory: Path) -> None:
    """End the reserved run while preserving edits to the current manifest."""
    path = directory / MANIFEST_FILENAME
    while True:
        revision = path.read_bytes()
        data = yaml.safe_load(revision)
        if data.get("status") != "running":
            return
        data["status"] = "failed"
        try:
            _write_manifest(path, data, revision)
            return
        except ManifestConflict:
            continue


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

    def __post_init__(self) -> None:
        # File state is separate from the fields serialized to experiment.yaml.
        self._source_path: Path | None = None
        self.revision: bytes | None = None

    @property
    def revision_token(self) -> str:
        return hashlib.sha256(self.revision).hexdigest() if self.revision is not None else ""

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

    def save(self, directory: Path, *, output_text: str | None = None) -> Path:
        """Commit the manifest and any runner output against the loaded revision."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / MANIFEST_FILENAME
        target = path.resolve()
        expected = self.revision if self._source_path == target else _current_revision(path)
        self.revision = _write_manifest(path, self.to_dict(), expected, output_text=output_text)
        self._source_path = target
        logger.debug("Saved manifest %r to %s", self.id, path)
        return path

    # -- Deserialisation ------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Manifest:
        """Load a manifest from a YAML file."""
        path = Path(path)
        if path.is_dir():
            path = path / MANIFEST_FILENAME
        revision = path.read_bytes()
        data = yaml.safe_load(revision) or {}
        manifest = cls._from_dict(data)
        manifest._source_path = path.resolve()
        manifest.revision = revision
        logger.debug("Loaded manifest %r from %s", manifest.id, path)
        return manifest

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
    if not root.is_dir():
        return []
    return sorted(
        p.parent
        for p in root.rglob(MANIFEST_FILENAME)
        if p.is_file()
    )


def load_all_manifests(root: Path) -> list[Manifest]:
    """Load every manifest found under *root*."""
    return [Manifest.load(d) for d in find_experiments(root)]


def build_lineage(manifests: list[Manifest]) -> dict[str, list[str]]:
    """Build a parent -> children mapping from a list of manifests."""
    tree: dict[str, list[str]] = {}
    for m in manifests:
        tree.setdefault(m.id, [])
        if m.baseline:
            tree.setdefault(m.baseline, []).append(m.id)
    return tree
