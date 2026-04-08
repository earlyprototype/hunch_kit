"""Tests for campaign project config and root discovery."""

from pathlib import Path

import pytest

from hunch_kit.project import (
    PROJECT_FILENAME,
    ProjectConfig,
    find_project_root,
    resolve_campaign_root,
    infer_campaign_root_from_experiment_dir,
)


class TestFindProjectRoot:
    def test_finds_yaml_in_start_dir(self, tmp_path: Path) -> None:
        (tmp_path / PROJECT_FILENAME).write_text("name: test\n", encoding="utf-8")
        assert find_project_root(tmp_path) == tmp_path.resolve()

    def test_walks_upward(self, tmp_path: Path) -> None:
        (tmp_path / PROJECT_FILENAME).write_text("name: test\n", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_project_root(nested) == tmp_path.resolve()

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert find_project_root(tmp_path) is None


class TestResolveCampaignRoot:
    def test_explicit_wins(self, tmp_path: Path) -> None:
        r = resolve_campaign_root(explicit=str(tmp_path))
        assert r == tmp_path.resolve()

    def test_finds_project_when_no_explicit(self, tmp_path: Path) -> None:
        (tmp_path / PROJECT_FILENAME).touch()
        nested = tmp_path / "sub"
        nested.mkdir()
        assert resolve_campaign_root(explicit=None, start=nested) == tmp_path.resolve()

    def test_fallback_uses_cwd_when_no_project_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If no ``hunch_project.yaml`` is found, fall back to ``Path.cwd()``."""
        monkeypatch.chdir(tmp_path)
        assert resolve_campaign_root(explicit=None, start=tmp_path) == tmp_path.resolve()


class TestInferCampaignRootFromExperiment:
    def test_find_project_from_experiment_dir(self, tmp_path: Path) -> None:
        (tmp_path / PROJECT_FILENAME).write_text("name: x\n", encoding="utf-8")
        exp = tmp_path / "experiments" / "ex_1"
        exp.mkdir(parents=True)
        assert infer_campaign_root_from_experiment_dir(exp) == tmp_path.resolve()

    def test_experiments_parent_heuristic(self, tmp_path: Path) -> None:
        exp = tmp_path / "experiments" / "ex_1"
        exp.mkdir(parents=True)
        assert infer_campaign_root_from_experiment_dir(exp) == tmp_path.resolve()


class TestProjectConfigRoundTrip:
    def test_save_and_load(self, tmp_path: Path) -> None:
        cfg = ProjectConfig(
            name="my_campaign",
            description="desc",
            default_provider="echo",
            default_rubric="r1",
            providers_path="providers",
        )
        cfg.save(tmp_path)
        loaded = ProjectConfig.load(tmp_path)
        assert loaded.name == "my_campaign"
        assert loaded.default_provider == "echo"
        assert loaded.default_rubric == "r1"

    def test_load_missing_returns_defaults(self, tmp_path: Path) -> None:
        loaded = ProjectConfig.load(tmp_path)
        assert loaded.default_provider == "echo"
        assert loaded.providers_path == "providers"
