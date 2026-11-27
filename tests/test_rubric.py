"""Tests for rubric schema and YAML I/O."""

import os
from pathlib import Path

import pytest

from hunch_kit.rubric import Rubric, Dimension, find_rubrics, load_rubric_by_name


class TestDimension:
    def test_valid_dimension(self):
        d = Dimension(name="quality", description="Overall quality")
        assert d.validate() == []

    def test_missing_name(self):
        d = Dimension(name="", description="No name")
        errors = d.validate()
        assert any("name" in e for e in errors)

    def test_invalid_evaluator(self):
        d = Dimension(name="test", evaluator="invalid")
        errors = d.validate()
        assert any("evaluator" in e for e in errors)

    def test_invalid_scale(self):
        d = Dimension(name="test", scale_min=10, scale_max=1)
        errors = d.validate()
        assert any("scale" in e for e in errors)


class TestRubric:
    def test_valid_rubric(self):
        r = Rubric(
            name="test_rubric",
            dimensions=[Dimension(name="quality", description="Overall quality")],
        )
        assert r.validate() == []

    def test_missing_name(self):
        r = Rubric(dimensions=[Dimension(name="q")])
        errors = r.validate()
        assert any("name" in e.lower() for e in errors)

    def test_no_dimensions(self):
        r = Rubric(name="empty")
        errors = r.validate()
        assert any("dimension" in e.lower() for e in errors)

    def test_duplicate_dimension_names(self):
        r = Rubric(
            name="dupes",
            dimensions=[
                Dimension(name="quality"),
                Dimension(name="quality"),
            ],
        )
        errors = r.validate()
        assert any("duplicate" in e.lower() for e in errors)


class TestRubricRoundTrip:
    def test_save_and_load(self, tmp_project: Path):
        rubric_dir = tmp_project / "rubrics"
        original = Rubric(
            name="test_rubric",
            description="A test rubric",
            dimensions=[
                Dimension(
                    name="quality",
                    description="Overall quality",
                    scale_min=1,
                    scale_max=10,
                    evaluator="human",
                    anchors={1: "Terrible", 5: "Adequate", 10: "Perfect"},
                ),
                Dimension(
                    name="accuracy",
                    description="Factual correctness",
                    evaluator="both",
                ),
            ],
        )

        path = original.save(rubric_dir)
        assert path.exists()

        loaded = Rubric.load(path)
        assert loaded.name == "test_rubric"
        assert len(loaded.dimensions) == 2
        assert loaded.dimensions[0].anchors[1] == "Terrible"

    def test_human_dimensions_filter(self):
        r = Rubric(
            name="mixed",
            dimensions=[
                Dimension(name="visual", evaluator="human"),
                Dimension(name="content", evaluator="llm"),
                Dimension(name="accuracy", evaluator="both"),
            ],
        )
        human = r.human_dimensions()
        assert len(human) == 2
        assert {d.name for d in human} == {"visual", "accuracy"}

    def test_llm_dimensions_filter(self):
        r = Rubric(
            name="mixed",
            dimensions=[
                Dimension(name="visual", evaluator="human"),
                Dimension(name="content", evaluator="llm"),
                Dimension(name="accuracy", evaluator="both"),
            ],
        )
        llm = r.llm_dimensions()
        assert len(llm) == 2
        assert {d.name for d in llm} == {"content", "accuracy"}


class TestDiscovery:
    def test_find_rubrics_ignores_templates(self, tmp_project: Path):
        rubric_dir = tmp_project / "rubrics"
        (rubric_dir / "_template.yaml").write_text("name: template")
        Rubric(name="real", dimensions=[Dimension(name="q")]).save(rubric_dir)

        found = find_rubrics(rubric_dir)
        assert len(found) == 1
        assert found[0].name != "_template.yaml"

    def test_load_by_name(self, tmp_project: Path):
        rubric_dir = tmp_project / "rubrics"
        Rubric(
            name="slide_quality",
            dimensions=[Dimension(name="visual")],
        ).save(rubric_dir)

        loaded = load_rubric_by_name(rubric_dir, "slide_quality")
        assert loaded is not None
        assert loaded.name == "slide_quality"

    def test_load_by_name_not_found(self, tmp_project: Path):
        rubric_dir = tmp_project / "rubrics"
        loaded = load_rubric_by_name(rubric_dir, "nonexistent")
        assert loaded is None

    @pytest.mark.parametrize("name", ["../other", r"..\other", "/other", r"\\offline.invalid\rubric", "D:other"])
    def test_path_reference_rejected_before_filesystem_access(self, name, monkeypatch):
        def reject_io(*args, **kwargs):
            pytest.fail("Rubric lookup touched the filesystem for a path reference")

        with monkeypatch.context() as guard:
            for method in ("stat", "lstat", "resolve", "iterdir", "glob", "open"):
                guard.setattr(Path, method, reject_io)
            assert load_rubric_by_name(Path("rubrics"), name) is None

    def test_lookup_by_declared_name(self, tmp_project: Path):
        rubric_dir = tmp_project / "rubrics"
        path = Rubric(name="Human Review", dimensions=[Dimension("quality")]).save(rubric_dir)
        path.rename(rubric_dir / "local.yaml")

        assert load_rubric_by_name(rubric_dir, "human review").name == "Human Review"

    def test_lookup_by_punctuated_display_name(self, tmp_project: Path):
        directory = tmp_project / "rubrics"
        (directory / "clarity.yaml").write_text(
            "name: 'How clear?'\ndimensions:\n- name: clarity\n", encoding="utf-8",
        )

        rubric = load_rubric_by_name(directory, "how clear?")
        assert rubric is not None
        assert rubric.name == "How clear?"

    def test_external_symlink_ignored_in_direct_and_declared_name_lookup(self, tmp_project: Path):
        rubric_dir = tmp_project / "rubrics"
        outside = Rubric(name="Other", dimensions=[Dimension("quality")]).save(tmp_project)
        try:
            (rubric_dir / "alias.yaml").symlink_to(outside)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Creating file symlinks requires Windows symlink privileges")
            raise

        assert load_rubric_by_name(rubric_dir, "alias") is None
        assert load_rubric_by_name(rubric_dir, "Other") is None

    def test_linked_rubrics_directory_is_unavailable(self, tmp_path: Path):
        directory = tmp_path / "campaign" / "rubrics"
        directory.parent.mkdir()
        outside = tmp_path / "other"
        Rubric(name="Review", dimensions=[Dimension("quality")]).save(outside)
        if os.name == "nt":
            # A directory junction works without the Windows symlink privilege.
            from _winapi import CreateJunction
            CreateJunction(str(outside), str(directory))
        else:
            directory.symlink_to(outside, target_is_directory=True)

        assert load_rubric_by_name(directory, "Review") is None
