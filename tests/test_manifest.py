"""Tests for manifest schema and YAML I/O."""

from pathlib import Path

import pytest

from hunch_kit.manifest import (
    Manifest,
    find_experiments,
    load_all_manifests,
    build_lineage,
)


class TestManifestCreate:
    def test_create_with_required_fields(self):
        m = Manifest.create(
            experiment_id="ex_001",
            hypothesis="Testing creates a valid manifest",
            variable_changed="test_var",
            variable_value="new_value",
        )
        assert m.id == "ex_001"
        assert m.hypothesis == "Testing creates a valid manifest"
        assert m.status == "pending"
        assert m.date  # should be populated

    def test_create_with_baseline(self):
        m = Manifest.create(
            experiment_id="ex_002",
            hypothesis="Change improves quality",
            variable_changed="format",
            variable_value="compressed",
            baseline="ex_001",
        )
        assert m.baseline == "ex_001"


class TestManifestValidation:
    def test_valid_manifest(self):
        m = Manifest.create("ex_001", "h", "v", "val")
        assert m.validate() == []

    def test_missing_required_fields(self):
        m = Manifest()
        errors = m.validate()
        assert len(errors) >= 4
        assert any("id" in e for e in errors)
        assert any("hypothesis" in e for e in errors)


class TestManifestRoundTrip:
    def test_save_and_load(self, tmp_project: Path):
        exp_dir = tmp_project / "experiments" / "ex_001"
        original = Manifest.create(
            experiment_id="ex_001",
            hypothesis="Round-trip preserves data",
            variable_changed="format",
            variable_value="yaml",
            baseline="baseline_001",
            provider="echo",
        )
        original.human_scores = {"quality": 8.0, "accuracy": 7.5}
        original.overall_preference = "better"
        original.notes = "Test note"

        original.save(exp_dir)
        loaded = Manifest.load(exp_dir)

        assert loaded.id == "ex_001"
        assert loaded.hypothesis == "Round-trip preserves data"
        assert loaded.baseline == "baseline_001"
        assert loaded.human_scores == {"quality": 8.0, "accuracy": 7.5}
        assert loaded.overall_preference == "better"
        assert loaded.notes == "Test note"

    def test_to_dict_excludes_empty(self):
        m = Manifest.create("ex_001", "h", "v", "val")
        d = m.to_dict()
        assert "children" not in d  # empty list excluded
        assert "id" in d


class TestDiscovery:
    def test_find_experiments(self, tmp_project: Path):
        exp_root = tmp_project / "experiments"
        for eid in ("ex_001", "ex_002", "ex_003"):
            Manifest.create(eid, "h", "v", "val").save(exp_root / eid)

        dirs = find_experiments(exp_root)
        assert len(dirs) == 3

    def test_load_all_manifests(self, tmp_project: Path):
        exp_root = tmp_project / "experiments"
        for eid in ("ex_001", "ex_002"):
            Manifest.create(eid, "h", "v", "val").save(exp_root / eid)

        manifests = load_all_manifests(exp_root)
        assert len(manifests) == 2
        ids = {m.id for m in manifests}
        assert ids == {"ex_001", "ex_002"}


class TestLineage:
    def test_build_lineage(self):
        manifests = [
            Manifest.create("root", "h", "v", "val"),
            Manifest.create("child_a", "h", "v", "val", baseline="root"),
            Manifest.create("child_b", "h", "v", "val", baseline="root"),
            Manifest.create("grandchild", "h", "v", "val", baseline="child_a"),
        ]
        tree = build_lineage(manifests)
        assert "child_a" in tree["root"]
        assert "child_b" in tree["root"]
        assert "grandchild" in tree["child_a"]
