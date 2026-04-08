"""Tests for the CLI — smoke tests via Click's CliRunner."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from hunch_kit.cli import cli
from hunch_kit.manifest import Manifest
from hunch_kit.project import ProjectConfig


@pytest.fixture
def campaign(tmp_path: Path) -> Path:
    """Create a minimal campaign directory with hunch_project.yaml."""
    cfg = ProjectConfig(name="test_campaign")
    cfg.save(tmp_path)
    (tmp_path / "experiments").mkdir()
    (tmp_path / "rubrics").mkdir()
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestInitProject:
    def test_scaffolds_campaign(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "my_campaign"
        result = runner.invoke(cli, [
            "init-project", str(target),
            "--name", "test", "--description", "desc",
        ])
        assert result.exit_code == 0, result.output
        assert (target / "hunch_project.yaml").exists()
        assert (target / "experiments").is_dir()
        assert (target / "rubrics").is_dir()
        assert (target / "providers").is_dir()

    def test_rejects_existing_campaign(self, campaign: Path, runner: CliRunner) -> None:
        result = runner.invoke(cli, [
            "init-project", str(campaign),
            "--name", "x", "--description", "x",
        ])
        assert result.exit_code != 0
        assert "Already a campaign" in result.output

    def test_tolerates_existing_subdirs(self, tmp_path: Path, runner: CliRunner) -> None:
        """Subdirs that already exist should not crash init-project."""
        (tmp_path / "experiments").mkdir()
        (tmp_path / "rubrics").mkdir()
        result = runner.invoke(cli, [
            "init-project", str(tmp_path),
            "--name", "test", "--description", "desc",
        ])
        assert result.exit_code == 0, result.output


class TestInit:
    def test_scaffolds_experiment(self, campaign: Path, runner: CliRunner) -> None:
        result = runner.invoke(cli, [
            "--root", str(campaign),
            "init",
            "--id", "ex_001",
            "--hypothesis", "Test hypothesis",
            "--variable", "test_var",
            "--value", "new_val",
            "--baseline", "",
        ])
        assert result.exit_code == 0, result.output
        assert "Created" in result.output
        exp_dir = campaign / "experiments" / "ex_001"
        assert exp_dir.exists()
        assert (exp_dir / "experiment.yaml").exists()

    def test_rejects_duplicate_id(self, campaign: Path, runner: CliRunner) -> None:
        args = [
            "--root", str(campaign),
            "init",
            "--id", "ex_dup",
            "--hypothesis", "h",
            "--variable", "v",
            "--value", "val",
            "--baseline", "",
        ]
        runner.invoke(cli, args)
        result = runner.invoke(cli, args)
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_updates_parent_children(self, campaign: Path, runner: CliRunner) -> None:
        runner.invoke(cli, [
            "--root", str(campaign),
            "init", "--id", "parent", "--hypothesis", "h",
            "--variable", "v", "--value", "val", "--baseline", "",
        ])
        runner.invoke(cli, [
            "--root", str(campaign),
            "init", "--id", "child", "--hypothesis", "h",
            "--variable", "v", "--value", "val", "--baseline", "parent",
        ])
        parent = Manifest.load(campaign / "experiments" / "parent")
        assert "child" in parent.children


class TestRun:
    def test_run_echo_provider(self, campaign: Path, runner: CliRunner) -> None:
        exp_dir = campaign / "experiments" / "ex_run"
        m = Manifest(
            id="ex_run", hypothesis="h",
            variable_changed="v", variable_value="val",
            provider="echo", input_path="in.txt",
        )
        m.save(exp_dir)
        (exp_dir / "output").mkdir()
        (exp_dir / "in.txt").write_text("hello world", encoding="utf-8")

        result = runner.invoke(cli, ["--root", str(campaign), "run", "ex_run"])
        assert result.exit_code == 0
        assert "success" in result.output

    def test_run_missing_experiment(self, campaign: Path, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--root", str(campaign), "run", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestList:
    def test_empty_campaign(self, campaign: Path, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--root", str(campaign), "list"])
        assert result.exit_code == 0
        assert "No experiments" in result.output

    def test_lists_experiments(self, campaign: Path, runner: CliRunner) -> None:
        Manifest(
            id="ex_a", hypothesis="h",
            variable_changed="v", variable_value="val",
        ).save(campaign / "experiments" / "ex_a")
        Manifest(
            id="ex_b", hypothesis="h",
            variable_changed="v", variable_value="val",
        ).save(campaign / "experiments" / "ex_b")

        result = runner.invoke(cli, ["--root", str(campaign), "list"])
        assert result.exit_code == 0
        assert "ex_a" in result.output
        assert "ex_b" in result.output


class TestLineage:
    def test_prints_tree(self, campaign: Path, runner: CliRunner) -> None:
        exp = campaign / "experiments"
        Manifest(id="root", hypothesis="h", variable_changed="v", variable_value="val").save(exp / "root")
        Manifest(id="child_a", hypothesis="h", variable_changed="v", variable_value="val", baseline="root").save(exp / "child_a")
        Manifest(id="child_b", hypothesis="h", variable_changed="v", variable_value="val", baseline="root").save(exp / "child_b")
        Manifest(id="grandchild", hypothesis="h", variable_changed="v", variable_value="val", baseline="child_a").save(exp / "grandchild")

        result = runner.invoke(cli, ["--root", str(campaign), "lineage"])
        assert result.exit_code == 0
        assert "root" in result.output
        assert "child_a" in result.output
        assert "grandchild" in result.output

    def test_tree_indentation_is_correct(self, campaign: Path, runner: CliRunner) -> None:
        """Verify _print_tree produces correct box-drawing indentation."""
        exp = campaign / "experiments"
        Manifest(id="root", hypothesis="h", variable_changed="v", variable_value="val").save(exp / "root")
        Manifest(id="child", hypothesis="h", variable_changed="v", variable_value="val", baseline="root").save(exp / "child")
        Manifest(id="grandchild", hypothesis="h", variable_changed="v", variable_value="val", baseline="child").save(exp / "grandchild")

        result = runner.invoke(cli, ["--root", str(campaign), "lineage"])
        lines = result.output.strip().splitlines()

        assert lines[0] == "root"
        assert lines[1] == "└── child"
        assert lines[2] == "    └── grandchild"

    def test_tree_siblings_indentation(self, campaign: Path, runner: CliRunner) -> None:
        """Verify continuation lines (│) for siblings."""
        exp = campaign / "experiments"
        Manifest(id="root", hypothesis="h", variable_changed="v", variable_value="val").save(exp / "root")
        Manifest(id="child_a", hypothesis="h", variable_changed="v", variable_value="val", baseline="root").save(exp / "child_a")
        Manifest(id="child_b", hypothesis="h", variable_changed="v", variable_value="val", baseline="root").save(exp / "child_b")
        Manifest(id="grandchild", hypothesis="h", variable_changed="v", variable_value="val", baseline="child_a").save(exp / "grandchild")

        result = runner.invoke(cli, ["--root", str(campaign), "lineage"])
        lines = result.output.strip().splitlines()

        assert lines[0] == "root"
        assert lines[1] == "├── child_a"
        assert lines[2] == "│   └── grandchild"
        assert lines[3] == "└── child_b"

    def test_lineage_nonexistent_experiment_in_populated_tree(
        self, campaign: Path, runner: CliRunner,
    ) -> None:
        """Requesting a nonexistent ID in a tree with experiments should error."""
        exp = campaign / "experiments"
        Manifest(id="root", hypothesis="h", variable_changed="v", variable_value="val").save(exp / "root")

        result = runner.invoke(cli, ["--root", str(campaign), "lineage", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output
