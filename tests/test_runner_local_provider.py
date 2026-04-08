"""Tests for local campaign provider discovery."""

from pathlib import Path

from hunch_kit.manifest import Manifest
from hunch_kit.runner import (
    discover_local_provider_classes,
    resolve_provider,
    run_experiment,
)


def _write_local_provider(providers_dir: Path) -> None:
    providers_dir.mkdir(parents=True, exist_ok=True)
    (providers_dir / "local_echo.py").write_text(
        """
from hunch_kit.providers.base import BaseProvider, ProviderResult

class LocalEchoProvider(BaseProvider):
    name = "local_echo"

    def run(self, input_text, config=None):
        return ProviderResult(
            output="LOCAL:" + input_text,
            status="success",
            metadata={"via": "local"},
        )
""",
        encoding="utf-8",
    )


def test_discover_local_provider_classes(tmp_path: Path) -> None:
    (tmp_path / "hunch_project.yaml").write_text(
        "name: t\nproviders_path: providers\n", encoding="utf-8"
    )
    _write_local_provider(tmp_path / "providers")

    found = discover_local_provider_classes(tmp_path)
    assert "local_echo" in found
    assert found["local_echo"].name == "local_echo"


def test_resolve_provider_prefers_local_over_builtin(tmp_path: Path) -> None:
    (tmp_path / "hunch_project.yaml").write_text(
        "name: t\nproviders_path: providers\n", encoding="utf-8"
    )
    _write_local_provider(tmp_path / "providers")

    p = resolve_provider("local_echo", campaign_root=tmp_path)
    r = p.run("hi")
    assert r.output == "LOCAL:hi"


def test_run_experiment_uses_local_provider(tmp_path: Path) -> None:
    (tmp_path / "hunch_project.yaml").write_text(
        "name: t\nproviders_path: providers\n", encoding="utf-8"
    )
    _write_local_provider(tmp_path / "providers")

    exp_root = tmp_path / "experiments" / "ex_local"
    exp_root.mkdir(parents=True)
    (exp_root / "output").mkdir(exist_ok=True)

    m = Manifest.create(
        experiment_id="ex_local",
        hypothesis="h",
        variable_changed="v",
        variable_value="val",
        provider="local_echo",
        input_path="in.txt",
    )
    (exp_root / "in.txt").write_text("data", encoding="utf-8")
    m.save(exp_root)

    result = run_experiment(exp_root)
    assert result.status == "success"
    assert "LOCAL:data" in result.output
