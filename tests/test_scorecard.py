"""Offline scorecard checks through the public CLI."""

from pathlib import Path

from click.testing import CliRunner

from hunch_kit.cli import cli
from hunch_kit.manifest import Manifest
from hunch_kit.rubric import Dimension, Rubric


def export(campaign: Path) -> str:
    result = CliRunner().invoke(cli, ["--root", str(campaign), "scorecard"])
    assert result.exit_code == 0, result.output or str(result.exception)
    return result.output


def section(report: str, experiment: str) -> str:
    return report.split(f"\n## `{experiment}`\n", 1)[1].partition("\n## ")[0]


def test_design_thinking_export_is_repeatable_and_read_only() -> None:
    campaign = Path(__file__).resolve().parents[1] / "examples" / "design_thinking"
    sources = sorted(campaign.rglob("*.yaml"))
    before = [path.read_bytes() for path in sources]

    report = export(campaign)
    assert export(campaign) == report
    assert [path.read_bytes() for path in sources] == before
    assert str(campaign) not in report
    assert "# Campaign scorecard: `design_thinking`" in report
    baseline = section(report, "dt_v1")
    assert "Parent: Root" in baseline
    assert "| `text_accuracy` | n/a | 3 | Root |" in baseline
    revision = section(report, "dt_v3")
    assert "Parent: `dt_v2`" in revision
    assert (
        "Changed variable: `heading_typography` = "
        "`plain bold sans + spell-exactly rule (was serif/sans mix)`"
    ) in revision
    assert "| `text_accuracy` | 6 | 10 | +4 |" in revision
    assert "| `visual_fidelity` | 9 | 8 | -1 |" in revision


def test_fork_uses_each_childs_own_parent_and_only_human_scores(tmp_project: Path) -> None:
    rubric = Rubric(name="Review", dimensions=[
        Dimension("clarity", scale_min=0, evaluator="both"),
        Dimension("style"),
        Dimension("latency", evaluator="llm"),
    ])
    rubric.save(tmp_project / "rubrics")
    for experiment, parent, scores in [
        ("original", "", {"clarity": 2, "style": 4}),
        ("compact", "original", {"clarity": 6, "style": 4}),
        ("large", "original", {"clarity": 0}),
        ("draft", "large", {}),
    ]:
        Manifest(
            id=experiment, baseline=parent, rubric="Review", variable_changed="layout",
            variable_value=experiment, human_scores=scores, automated_scores={"latency": 7},
        ).save(tmp_project / "experiments" / experiment)

    report = export(tmp_project)
    assert "| `clarity` | 2 | 6 | +4 |" in section(report, "compact")
    assert "| `style` | 4 | 4 | +0 |" in section(report, "compact")
    large = section(report, "large")
    assert "| `clarity` | 2 | 0 | -2 |" in large
    assert "| `style` | 4 | Missing | Missing score |" in large
    draft = section(report, "draft")
    assert "Parent: `large`" in draft
    assert "| `clarity` | 0 | Missing | Missing score |" in draft
    assert "| `style` | Missing | Missing | Missing score |" in draft
    assert "latency" not in report


def test_rubric_compatibility_is_checked_per_dimension(tmp_project: Path) -> None:
    rubric_dir = tmp_project / "rubrics"
    Rubric(name="First", dimensions=[
        Dimension("clarity"), Dimension("tone", scale_max=5), Dimension("spacing"),
        Dimension("contrast", anchors={1: "clear", 10: "unclear"}),
    ]).save(rubric_dir)
    Rubric(name="Second", dimensions=[
        Dimension("clarity"), Dimension("tone"), Dimension("detail"),
        Dimension("contrast", anchors={1: "unclear", 10: "clear"}),
    ]).save(rubric_dir)
    exp_dir = tmp_project / "experiments"
    Manifest(id="a", rubric="First", human_scores={
        "clarity": 6.5, "tone": 4, "spacing": 3, "contrast": 3,
    }).save(exp_dir / "a")
    Manifest(id="b", baseline="a", rubric="Second", human_scores={
        "clarity": 7, "tone": 8, "detail": 6, "contrast": 7,
    }).save(exp_dir / "b")

    child = section(export(tmp_project), "b")
    assert "| `clarity` | 6.5 | 7 | +0.5 |" in child
    assert "| `tone` | 4 | 8 | Incomparable rubrics |" in child
    assert "| `spacing` | 3 | Missing | Incomparable rubrics |" in child
    assert "| `detail` | Missing | 6 | Incomparable rubrics |" in child
    assert "| `contrast` | 3 | 7 | Incomparable rubrics |" in child


def test_missing_rubric_and_parent_preserve_recorded_scores(tmp_project: Path) -> None:
    exp_dir = tmp_project / "experiments"
    Manifest(id="a", baseline="absent", rubric="Lost", human_scores={"detail": 4}).save(exp_dir / "a")
    Manifest(id="b", baseline="a", rubric="Lost", human_scores={"detail": 6}).save(exp_dir / "b")

    report = export(tmp_project)
    orphan = section(report, "a")
    assert "Parent: `absent` (missing manifest)" in orphan
    assert "Rubric: `Lost` (unavailable)" in orphan
    assert "| `detail` | n/a | 4 | Missing parent |" in orphan
    assert "| `detail` | 4 | 6 | Incomparable rubrics |" in section(report, "b")


def test_empty_scores_and_literal_markdown_in_manifests(tmp_project: Path) -> None:
    exp_dir = tmp_project / "experiments"
    Rubric(name="Review", dimensions=[Dimension("look|feel")]).save(tmp_project / "rubrics")
    parent_path = Manifest(id="a", rubric="Review").save(exp_dir / "a")
    with parent_path.open("a", encoding="utf-8") as file:
        file.write("human_scores: null\n")
    Manifest(
        id="b", baseline="a", rubric="Review", variable_changed="label",
        variable_value="use `small`\nprint", human_scores={"look|feel": 2},
    ).save(exp_dir / "b")

    child = section(export(tmp_project), "b")
    assert "Changed variable: `label` = `` use `small` print ``" in child
    assert r"| `look\|feel` | Missing | 2 | Missing score |" in child
