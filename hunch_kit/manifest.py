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
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from filelock import FileLock, Timeout

from ._file_permissions import prepare_replacement

logger = logging.getLogger(__name__)


SCHEMA_VERSION = "1"
MANIFEST_FILENAME = "experiment.yaml"
LOCK_DIRECTORY = Path.home() / ".hunch-kit-locks"

REQUIRED_FIELDS = ("id", "hypothesis", "variable_changed", "variable_value")


def _manifest_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)


class ManifestConflict(RuntimeError):
    """The manifest changed since this writer read it."""


def _local_state_path(directory: Path, suffix: str) -> Path:
    """Share locks and ballot revisions between local processes."""
    LOCK_DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    canonical = os.path.normcase(str(directory.resolve()))
    key = hashlib.sha256(os.fsencode(canonical)).hexdigest()
    return LOCK_DIRECTORY / f"{key}.{suffix}"


@contextmanager
def reserve_outputs(*directories: Path):
    """Exclude runs while a ballot commits, and overlapping runs on the same output."""
    with ExitStack() as locks:
        for path in sorted({_local_state_path(directory, "run.lock") for directory in directories}):
            try:
                locks.enter_context(FileLock(path, timeout=0))
            except Timeout as exc:
                raise ManifestConflict("An output is in use. Retry when the operation finishes.") from exc
        yield


def _current_revision(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def preference_revision(directory: Path) -> bytes:
    """Local ballot generation shared by evaluators of this campaign."""
    return _current_revision(_local_state_path(directory, "preference.rev")) or b""


@contextmanager
def _publish_output(path: Path, text: str):
    """Install a complete result and restore its predecessor if the manifest commit fails."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        new = Path(staging) / "new"
        old = Path(staging) / "old"
        if path.exists():
            prepare_replacement(path, new)
            prepare_replacement(path, old)
            shutil.copy2(path, old)
        new.write_text(text, encoding="utf-8")
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
    baseline_snapshot: tuple[Path, bytes | None] | None = None,
    reviewed_preference: bytes | None = None, judgment: bool = False,
) -> bytes:
    revision = _manifest_yaml(data).encode("utf-8")
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        # Normal file creation applies the caller's umask. The staging directory is private.
        temp_path = Path(staging) / "manifest.tmp"
        # A separate lock stays valid when the YAML file is atomically replaced.
        snapshots = {path: expected}
        if baseline_snapshot is not None:
            baseline_path, baseline_bytes = baseline_snapshot
            snapshots[baseline_path] = baseline_bytes
        with ExitStack() as locks:
            for lock in sorted({_local_state_path(p.parent, "manifest.lock") for p in snapshots}):
                locks.enter_context(FileLock(lock))
            if any(_current_revision(p) != snapshot for p, snapshot in snapshots.items()):
                raise ManifestConflict("The manifest changed. Reload it and retry.")
            if reviewed_preference is not None and preference_revision(path.parent) != reviewed_preference:
                raise ManifestConflict("The recorded preference changed. Start a new review.")
            if expected is not None:
                prepare_replacement(path, temp_path)
            temp_path.write_bytes(revision)
            output = nullcontext() if output_text is None else _publish_output(
                path.parent / data["output_path"], output_text,
            )
            with output:
                previous = (yaml.safe_load(expected) or {}) if expected else {}
                if judgment or data.get("overall_preference", "") != previous.get("overall_preference", ""):
                    # Invalidate open ballots before publishing, even for a repeat judgment.
                    # A failed publication can expire a ballot, but cannot revive a stale one.
                    marker = _local_state_path(path.parent, "preference.rev")
                    with tempfile.TemporaryDirectory(dir=marker.parent) as state_staging:
                        update = Path(state_staging) / "revision"
                        update.write_bytes(uuid4().bytes)
                        update.replace(marker)
                temp_path.replace(path)
    return revision


def save_overall_preference(
    directory: Path, preference: str, *, revision: bytes | None = None,
    baseline_snapshot: tuple[Path, bytes | None] | None = None, reviewed_preference: bytes | None = None,
) -> None:
    """Update just the preference in the reviewed revision, including extended manifests."""
    path = directory / MANIFEST_FILENAME
    if revision is None:
        revision = path.read_bytes()
    data = yaml.safe_load(revision)
    data["overall_preference"] = preference
    _write_manifest(path, data, revision, baseline_snapshot=baseline_snapshot,
                    reviewed_preference=reviewed_preference, judgment=True)


def save_human_scores(directory: Path, update: dict[str, Any]) -> str:
    """Merge supplied scoring fields and return the saved revision token."""
    path = directory / MANIFEST_FILENAME
    scores = {}
    for name, value in update.get("scores", {}).items():
        try:
            scores[name] = float(value)
        except (TypeError, ValueError):
            scores[name] = value

    while True:
        revision = path.read_bytes()
        data = yaml.safe_load(revision)
        if data.get("status") == "running" or (
            "revision" in update and update["revision"] != hashlib.sha256(revision).hexdigest()
        ):
            raise ManifestConflict("Reload the scoring form after the latest change.")
        if scores:
            data.setdefault("human_scores", {}).update(scores)
        if update.get("overall_preference"):
            data["overall_preference"] = update["overall_preference"]
        if "notes" in update:
            data["notes"] = update["notes"]
        try:
            saved = _write_manifest(path, data, revision, judgment=bool(update.get("overall_preference")))
        except ManifestConflict:
            if "revision" in update:
                raise
            continue
        return hashlib.sha256(saved).hexdigest()


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
