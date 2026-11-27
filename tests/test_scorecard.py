"""Offline scorecard checks through the public CLI."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest
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


def export_bytes(campaign: Path, *, env: dict[str, str] | None = None) -> bytes:
    result = subprocess.run(
        [sys.executable, "-c", "from hunch_kit.cli import cli; cli()",
         "--root", str(campaign), "scorecard"],
        env=env, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_design_thinking_export_is_repeatable_and_read_only() -> None:
    campaign = Path(__file__).resolve().parents[1] / "examples" / "design_thinking"

    def contents():
        return {p: hashlib.sha256(p.read_bytes()).digest() for p in campaign.rglob("*") if p.is_file()}

    before = contents()
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(export_bytes, [campaign] * 3))
    assert reports[0] == reports[1] == reports[2]
    assert contents() == before
    report = reports[0].decode("utf-8")
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
        Dimension("quality", description="Higher scores mean fewer errors"),
        Dimension("coverage", anchors={1: "none", 10: "all"}),
    ]).save(rubric_dir)
    Rubric(name="Second", dimensions=[
        Dimension("clarity"), Dimension("tone"), Dimension("detail"),
        Dimension("contrast", anchors={1: "unclear", 10: "clear"}),
        Dimension("quality", description="Higher scores mean more errors"),
        Dimension("coverage", anchors={"1": "none", "10": "all"}),
    ]).save(rubric_dir)
    exp_dir = tmp_project / "experiments"
    Manifest(id="a", rubric="First", human_scores={
        "clarity": 6.5, "tone": 4, "spacing": 3, "contrast": 3, "quality": 6, "coverage": 6,
    }).save(exp_dir / "a")
    Manifest(id="b", baseline="a", rubric="Second", human_scores={
        "clarity": 7, "tone": 8, "detail": 6, "contrast": 7, "quality": 8, "coverage": 8,
    }).save(exp_dir / "b")

    child = section(export(tmp_project), "b")
    assert "| `clarity` | 6.5 | 7 | +0.5 |" in child
    assert "| `tone` | 4 | 8 | Incomparable rubrics |" in child
    assert "| `spacing` | 3 | Missing | Incomparable rubrics |" in child
    assert "| `detail` | Missing | 6 | Incomparable rubrics |" in child
    assert "| `contrast` | 3 | 7 | Incomparable rubrics |" in child
    assert "| `quality` | 6 | 8 | Incomparable rubrics |" in child
    assert "| `coverage` | 6 | 8 | +2 |" in child


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


def test_opposite_rubric_instructions_prevent_a_delta(tmp_project: Path) -> None:
    for experiment, parent, rubric, instructions, score in [
        ("a", "", "First", "10 is best", 6),
        ("b", "a", "Second", "1 is best", 8),
    ]:
        Rubric(rubric, instructions, [Dimension("clarity")]).save(tmp_project / "rubrics")
        Manifest(id=experiment, baseline=parent, rubric=rubric, human_scores={"clarity": score}).save(
            tmp_project / "experiments" / experiment
        )

    assert "| `clarity` | 6 | 8 | Incomparable rubrics |" in section(export(tmp_project), "b")


def test_ambiguous_declared_name_requires_a_filename_for_comparison(tmp_project: Path) -> None:
    directory = tmp_project / "rubrics"
    for stem, maximum, instructions in [("first", 10, "Higher is better"), ("second", 5, "Lower is better")]:
        path = Rubric("Review", instructions, [Dimension("clarity", scale_max=maximum)]).save(directory)
        path.rename(directory / f"{stem}.yaml")
    for experiment, parent, reference, score in [
        ("baseline", "", "first", 2),
        ("ambiguous", "baseline", "Review", 4),
        ("pinned", "baseline", "first.yaml", 4),
    ]:
        Manifest(id=experiment, baseline=parent, rubric=reference, human_scores={"clarity": score}).save(
            tmp_project / "experiments" / experiment
        )

    report = export(tmp_project)
    assert "| `clarity` | 2 | 4 | Incomparable rubrics |" in section(report, "ambiguous")
    assert "| `clarity` | 2 | 4 | +2 |" in section(report, "pinned")


def test_declared_name_and_literal_stem_resolve_to_same_rubric(tmp_project: Path) -> None:
    directory = tmp_project / "rubrics"
    (directory / "Human Review.yaml").write_text(
        "name: 'UX: Clarity'\ndimensions:\n- name: clarity\n", encoding="utf-8",
    )
    # Both the normalized alias and a declared name compete with the literal stem.
    (directory / "human_review.yaml").write_text(
        "name: Other\ndimensions:\n- name: clarity\n  scale_max: 5\n", encoding="utf-8",
    )
    (directory / "earlier.yaml").write_text(
        "name: Human Review\ndimensions:\n- name: clarity\n  scale_max: 3\n", encoding="utf-8",
    )
    for experiment, parent, reference, score in [
        ("baseline", "", "UX: Clarity", 2), ("revision", "baseline", "Human Review", 4),
    ]:
        Manifest(id=experiment, baseline=parent, rubric=reference, human_scores={"clarity": score}).save(
            tmp_project / "experiments" / experiment
        )

    assert "| `clarity` | 2 | 4 | +2 |" in section(export(tmp_project), "revision")


@pytest.mark.parametrize("draft", [
    "name: Draft\ndimensions:\n", "name: Draft\ndimensions: []\n",
    "name: Draft\ndimensions: [unfinished", "name: Draft\ndimensions: [clarity]\n",
])
def test_unusable_rubric_does_not_hide_scores_or_other_rubrics(tmp_project: Path, draft: str) -> None:
    directory = tmp_project / "rubrics"
    (directory / "draft.yaml").write_text(draft, encoding="utf-8")
    path = Rubric("Review", dimensions=[Dimension("clarity")]).save(directory)
    path.rename(directory / "published.yaml")
    for experiment, parent, reference, score in [
        ("baseline", "", "Review", 2), ("revision", "baseline", "Review", 4),
        ("unfinished", "baseline", "draft", 3),
    ]:
        Manifest(id=experiment, baseline=parent, rubric=reference, human_scores={"clarity": score}).save(
            tmp_project / "experiments" / experiment
        )

    report = export(tmp_project)
    assert "| `clarity` | 2 | 4 | +2 |" in section(report, "revision")
    unfinished = section(report, "unfinished")
    assert "Rubric: `draft` (unavailable)" in unfinished
    assert "| `clarity` | 2 | 3 | Incomparable rubrics |" in unfinished


def test_duplicate_dimension_prevents_only_its_own_delta(tmp_project: Path) -> None:
    Rubric(name="Review", dimensions=[
        Dimension("clarity", scale_max=5), Dimension("clarity"), Dimension("tone"),
    ]).save(tmp_project / "rubrics")
    for experiment, parent, score in [("a", "", 2), ("b", "a", 8)]:
        Manifest(id=experiment, baseline=parent, rubric="Review", human_scores={
            "clarity": score, "tone": score,
        }).save(tmp_project / "experiments" / experiment)

    child = section(export(tmp_project), "b")
    assert "| `clarity` | 2 | 8 | Incomparable rubrics |" in child
    assert "| `tone` | 2 | 8 | +6 |" in child


@pytest.mark.parametrize(("low", "high", "change"), [
    ('"1"', '"10"', "+2"),
    ("1", "10", "+2"),
    ('"small"', '"large"', "Incomparable rubrics"),
    ("null", "10", "Incomparable rubrics"),
    ("10", "1", "Incomparable rubrics"),
    (".nan", ".inf", "Incomparable rubrics"),
])
def test_scale_bounds_are_numeric_and_finite(tmp_project: Path, low, high, change) -> None:
    directory = tmp_project / "rubrics"
    for name, minimum, maximum in [("First", '"1"', '"10"'), ("Second", low, high)]:
        (directory / f"{name}.yaml").write_text(
            f"name: {name}\ndimensions:\n- name: quality\n  scale_min: {minimum}\n"
            f"  scale_max: {maximum}\n- name: tone\n", encoding="utf-8",
        )
    for experiment, parent, rubric, score in [
        ("a", "", "First", 6), ("b", "a", "Second", 8), ("c", "b", "Second", 10),
    ]:
        Manifest(id=experiment, baseline=parent, rubric=rubric, human_scores={
            "quality": score, "tone": score,
        }).save(tmp_project / "experiments" / experiment)

    report = export(tmp_project)
    child = section(report, "b")
    assert f"| `quality` | 6 | 8 | {change} |" in child
    assert "| `tone` | 6 | 8 | +2 |" in child
    assert f"| `quality` | 8 | 10 | {change} |" in section(report, "c")


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_score_and_zero(tmp_project: Path, blank: str) -> None:
    Rubric(name="Review", dimensions=[Dimension("quality", scale_min=0)]).save(tmp_project / "rubrics")
    for experiment, parent, score in [("a", "", 3), ("b", "a", blank), ("c", "b", 0), ("d", "a", 0)]:
        Manifest(id=experiment, baseline=parent, rubric="Review", human_scores={"quality": score}).save(
            tmp_project / "experiments" / experiment
        )

    report = export(tmp_project)
    assert "| `quality` | 3 | Missing | Missing score |" in section(report, "b")
    assert "| `quality` | Missing | 0 | Missing score |" in section(report, "c")
    assert "| `quality` | 3 | 0 | -3 |" in section(report, "d")


def test_external_rubric_reported_as_unavailable(tmp_project: Path) -> None:
    Rubric(name="Other", dimensions=[Dimension("detail")]).save(tmp_project)
    Manifest(id="sample", rubric="../other").save(tmp_project / "experiments" / "sample")

    assert "Rubric: `../other` (unavailable)" in export(tmp_project)


def test_non_string_rubric_reference_is_unavailable(tmp_project: Path) -> None:
    exp_dir = tmp_project / "experiments"
    path = Manifest(id="a", human_scores={"detail": 4}).save(exp_dir / "a")
    with path.open("a", encoding="utf-8") as file:
        file.write("rubric: [Review]\n")
    Rubric(name="Review", dimensions=[Dimension("detail")]).save(tmp_project / "rubrics")
    Manifest(id="b", baseline="a", rubric="Review", human_scores={"detail": 6}).save(exp_dir / "b")

    report = export(tmp_project)
    assert "(unavailable)" in section(report, "a")
    assert "| `detail` | 4 | 6 | Incomparable rubrics |" in section(report, "b")


def test_null_anchors_leave_the_description_as_the_scale_definition(tmp_project: Path) -> None:
    (tmp_project / "rubrics" / "review.yaml").write_text(
        "name: Review\ndimensions:\n- name: detail\n  description: More is better\n  anchors: null\n",
        encoding="utf-8",
    )
    exp_dir = tmp_project / "experiments"
    Manifest(id="a", rubric="Review", human_scores={"detail": 6}).save(exp_dir / "a")
    Manifest(id="b", baseline="a", rubric="Review", human_scores={"detail": 8}).save(exp_dir / "b")

    assert "| `detail` | 6 | 8 | +2 |" in section(export(tmp_project), "b")


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
    assert r"Changed variable: `label` = `` use `small`\nprint ``" in child
    assert r"| `look\|feel` | Missing | 2 | Missing score |" in child


def test_significant_whitespace_remains_distinct(tmp_project: Path) -> None:
    values = {
        "indent": ("  ", r"\u0020\u0020"),
        "line": ("first\nsecond", r"first\nsecond"),
        "space": ("first second", "first second"),
        "literal": (r"first\nsecond", r"first\\nsecond"),
    }
    for experiment, (value, _) in values.items():
        Manifest(id=experiment, variable_changed="text", variable_value=value).save(
            tmp_project / "experiments" / experiment
        )

    report = export(tmp_project)
    for experiment, (_, expected) in values.items():
        assert f"Changed variable: `text` = `{expected}`" in section(report, experiment)


@pytest.mark.parametrize("contents", ["", "id: '  '", "id: same\nbaseline: second"])
def test_ambiguous_identity_fails_before_markdown(tmp_project: Path, contents: str) -> None:
    exp_dir = tmp_project / "experiments"
    Manifest(id="same", baseline="first").save(exp_dir / "a")
    path = Manifest().save(exp_dir / "b")
    path.write_text(contents, encoding="utf-8")

    result = CliRunner().invoke(cli, ["--root", str(tmp_project), "scorecard"])
    assert result.exit_code != 0
    assert "# Campaign scorecard" not in result.output
    if "same" in contents:
        assert "duplicate" in result.output.lower() and "same" in result.output
    else:
        assert "empty" in result.output.lower() and "id" in result.output.lower()


def test_redirected_report_uses_utf8(tmp_project: Path) -> None:
    Manifest(id="sample", variable_changed="label", variable_value="見出し").save(
        tmp_project / "experiments" / "sample"
    )
    report = export_bytes(
        tmp_project,
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252:strict"},
    )
    assert "Changed variable: `label` = `見出し`".encode("utf-8") in report


def test_control_characters_are_visible_in_report_bytes(tmp_project: Path) -> None:
    path = Manifest(id="sample", variable_changed="label").save(tmp_project / "experiments" / "sample")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(r'variable_value: "A\0B\aC\x7fD\x87E\\u0000"' + "\n")

    result = CliRunner().invoke(cli, ["--root", str(tmp_project), "scorecard"])
    assert result.exit_code == 0
    assert rb"`A\u0000B\u0007C\u007fD\u0087E\\u0000`" in result.stdout_bytes
    assert b"\x00" not in result.stdout_bytes


def test_linked_manifest_is_skipped(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    experiments = campaign / "experiments"
    outside = Manifest(id="outside").save(tmp_path / "other")
    local = Manifest(id="local").save(experiments / "group" / "local")
    link = experiments / "alias" / local.name
    link.parent.mkdir()
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Creating file symlinks requires Windows symlink privileges")
        raise

    report = export(campaign)
    assert "## `local`" in report
    assert "outside" not in report


def test_reparse_files_are_excluded_by_open_handle(tmp_project: Path, monkeypatch) -> None:
    # Simulate reparse metadata on the opened files even when directory entries
    # look ordinary. This also runs on systems without file symlink privileges.
    config = tmp_project / "hunch_project.yaml"
    config.write_text("name: hidden_title\n", encoding="utf-8")
    rubric = Rubric(name="Review", dimensions=[Dimension("quality")]).save(tmp_project / "rubrics")
    experiments = tmp_project / "experiments"
    hidden = Manifest(id="hidden_experiment").save(experiments / "hidden")
    Manifest(id="local", rubric="Review").save(experiments / "local")
    marked = [path.stat() for path in (config, rubric, hidden)]
    fstat = os.fstat

    def read_handle(fd):
        info = fstat(fd)
        if any(os.path.samestat(info, target) for target in marked):
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return info

    monkeypatch.setattr(os, "fstat", read_handle)
    report = export(tmp_project)
    assert report.startswith("# Campaign scorecard\n")
    assert "hidden_experiment" not in report
    assert "Rubric: `Review` (unavailable)" in section(report, "local")


def link_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        from _winapi import CreateJunction
        CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("relative_link", ["experiments", "experiments/group"])
def test_linked_experiment_directories_are_skipped(tmp_path: Path, relative_link: str) -> None:
    campaign = tmp_path / "campaign"
    link = campaign / relative_link
    link.parent.mkdir(parents=True)
    outside = tmp_path / "other" / "experiments"
    Manifest(id="outside").save(outside / "sample")
    link_directory(link, outside)

    assert "No experiments found." in export(campaign)


@pytest.mark.parametrize("command", ["list", "lineage"])
def test_existing_commands_find_experiments_linked_within_campaign(tmp_path: Path, command: str) -> None:
    storage = tmp_path / "storage"
    Manifest(id="linked_baseline").save(storage / "sample")
    link_directory(tmp_path / "experiments", storage)

    result = CliRunner().invoke(cli, ["--root", str(tmp_path), command])
    assert result.exit_code == 0, result.output
    assert "linked_baseline" in result.output
    assert "No experiments found." in export(tmp_path)
