"""Regression coverage for the GitHub-rendered architecture diagram."""

import re
from pathlib import Path


def test_architecture_shows_the_shared_runner_in_mermaid():
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    architecture = readme.split("## Architecture\n", 1)[1].split("\n## ", 1)[0]
    diagram = re.search(r"^```mermaid\n(.*?)\n```$", architecture, re.M | re.S)

    assert diagram, "Expected a fenced Mermaid diagram under Architecture"
    graph = diagram.group(1)
    assert re.match(r"^(?:flowchart|graph) (?:TD|TB|BT|LR|RL)$", graph, re.M)
    for caller in ("cli", "mcp"):
        edge = rf"^\s*{caller}\s+-->(?:\|[^|\n]+\|)?\s+runner\b"
        assert re.search(edge, graph, re.M), f"Expected an edge from {caller} to the runner"
