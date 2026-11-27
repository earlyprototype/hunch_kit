"""Offline reviews of the checked-in design thinking outputs."""

import base64
import re
import secrets
import shutil
from html.parser import HTMLParser
from pathlib import Path

import httpx
import pytest
import yaml
from click.testing import CliRunner

from hunch_kit.cli import cli
from hunch_kit.evaluation.human import create_app


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "design_thinking"


class ReviewForm(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.images = {}
        self.votes = {}
        self.button_action = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            self.images[attrs["alt"]] = attrs["src"]
        if tag == "button" and "formaction" in attrs:
            self.button_action = attrs["formaction"]

    def handle_data(self, data):
        if self.button_action:
            self.votes[data.strip()] = self.button_action

    def handle_endtag(self, tag):
        if tag == "button":
            self.button_action = None


@pytest.fixture
def campaign(tmp_path):
    return shutil.copytree(EXAMPLE, tmp_path / "design_thinking")


def client_for(campaign, **options):
    app = create_app(campaign / "experiments", campaign / "rubrics", **options)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("child_slot", "vote", "preference"), [
    ("A", "A", "better"),
    ("A", "B", "worse"),
    ("B", "A", "worse"),
    ("B", "B", "better"),
    ("B", "tie", "equivalent"),
])
async def test_review_images(campaign, monkeypatch, child_slot, vote, preference):
    monkeypatch.setattr(secrets, "choice", lambda slots: child_slot)
    experiments = campaign / "experiments"
    baseline = experiments / "dt_v2" / "experiment.yaml"
    child = experiments / "dt_v3" / "experiment.yaml"
    baseline_bytes, child_bytes = baseline.read_bytes(), child.read_bytes()
    baseline_data, child_data = yaml.safe_load(baseline_bytes), yaml.safe_load(child_bytes)
    baseline_slot = "B" if child_slot == "A" else "A"

    async with client_for(campaign, filter_id="dt_v3", blind=True) as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert (await client.get(page.url)).content == page.content
        form = ReviewForm(page.text)
        for slot, exp_id in [(child_slot, "dt_v3"), (baseline_slot, "dt_v2")]:
            mime, encoded = form.images[f"Output {slot}"].split(",", 1)
            assert mime == "data:image/png;base64"
            assert base64.b64decode(encoded) == (experiments / exp_id / "output/result.png").read_bytes()

        markup = re.sub(r'data:image/png;base64,[^"]+', "", page.text)
        for data in [baseline_data, child_data]:
            for hidden in ["id", "hypothesis", "provider", "notes", "variable_value", "overall_preference"]:
                assert data[hidden] not in markup
            assert data["id"] not in str(page.url)
            assert all(name not in markup for name in data["human_scores"])
        assert child.read_bytes() == child_bytes
        assert baseline.read_bytes() == baseline_bytes

        assert form.votes.keys() == {"A", "B", "Tie"}
        invalid = await client.post(form.votes["A"].rsplit("/", 1)[0] + "/invalid")
        assert invalid.is_client_error
        assert child.read_bytes() == child_bytes

        result = await client.post(form.votes[vote.capitalize()])
        assert result.status_code == 200
        assert f"{child_slot} = dt_v3 (child)" in result.text
        assert f"{baseline_slot} = dt_v2 (baseline)" in result.text
        assert (await client.get(result.url)).text == result.text
        await client.post(form.votes["B" if vote == "A" else "A"])
        assert yaml.safe_load(child.read_bytes()) == {**child_data, "overall_preference": preference}
        assert baseline.read_bytes() == baseline_bytes


@pytest.mark.asyncio
async def test_ordinary_and_blind_entry_points(campaign):
    async with client_for(campaign) as client:
        root = await client.get("/eval/dt_v1")
        assert root.status_code == 200
        assert 'id="scoring-form"' in root.text
        assert "Blind comparison" not in root.text
        child = await client.get("/eval/dt_v3")
        link = re.search(r'<a href="([^"]+)">Blind comparison</a>', child.text)
        assert link
        review = await client.get(link[1])
        assert review.status_code == 200
        assert "dt_v3" not in str(review.url)
        assert 'id="scoring-form"' not in review.text


@pytest.mark.asyncio
async def test_text_review_updates_only_the_preference(tmp_project):
    experiments = tmp_project / "experiments"
    child = experiments / "second" / "experiment.yaml"
    for name, parent, output in [("first", "", "Warm <gold>"), ("second", "first", "Cool <blue>")]:
        directory = experiments / name
        directory.mkdir()
        (directory / "result.txt").write_text(output, encoding="utf-8")
        (directory / "experiment.yaml").write_text(yaml.safe_dump({
            "id": name, "baseline": parent, "output_path": "result.txt",
            "automated_scores": {"machine_rating": 91.234},
            "human_scores": {"reader_rating": 72.345}, "notes": "",
            "campaign_annotation": "paired design study",
        }), encoding="utf-8")

    async with client_for(tmp_project, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert "Warm &lt;gold&gt;" in page.text and "Cool &lt;blue&gt;" in page.text
        for hidden in ["machine_rating", "91.234", "reader_rating", "72.345", "paired design study"]:
            assert hidden not in page.text
        data = yaml.safe_load(child.read_bytes())
        data["notes"] = "A note added while the review was open."
        child.write_text(yaml.safe_dump(data), encoding="utf-8")
        result = await client.post(ReviewForm(page.text).votes["Tie"])
        assert result.status_code == 200
        assert yaml.safe_load(child.read_bytes()) == {**data, "overall_preference": "equivalent"}
        fresh = await client.get("/")
        assert fresh.url != page.url
        assert "equivalent" not in fresh.text


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["root", "missing baseline", "self baseline", "missing output", "binary"])
async def test_unavailable_comparison_preserves_manifests(campaign, problem):
    experiments = campaign / "experiments"
    selected = "dt_v1" if problem == "root" else "dt_v3"
    path = experiments / selected / "experiment.yaml"
    data = yaml.safe_load(path.read_bytes())
    if problem == "missing baseline":
        data["baseline"] = "missing"
    if problem == "self baseline":
        data["baseline"] = selected
    if problem in {"missing output", "binary"}:
        data["output_path"] = "identifying-filename.bin"
        if problem == "binary":
            (path.parent / data["output_path"]).write_bytes(b"binary")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    async with client_for(campaign, filter_id=selected, blind=True) as client:
        page = await client.get("/")
        assert page.is_client_error
        assert "identifying-filename" not in page.text
        assert selected not in page.text
        assert "formaction" not in page.text
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["baseline", "output changed", "output removed"])
async def test_stale_review_cannot_save_a_vote(campaign, change):
    experiments = campaign / "experiments"
    child = experiments / "dt_v3" / "experiment.yaml"
    image = experiments / "dt_v2" / "output/result.png"
    async with client_for(campaign, filter_id="dt_v3", blind=True) as client:
        page = await client.get("/")
        vote_url = ReviewForm(page.text).votes["Tie"]
        if change == "baseline":
            data = yaml.safe_load(child.read_bytes())
            data["baseline"] = "dt_v1"
            child.write_text(yaml.safe_dump(data), encoding="utf-8")
        elif change == "output removed":
            image.unlink()
        else:
            shutil.copyfile(experiments / "dt_v1" / "output/result.png", image)
        before = child.read_bytes()
        assert (await client.post(vote_url)).is_client_error
        assert child.read_bytes() == before


def test_blind_cli_requires_a_child():
    result = CliRunner().invoke(cli, ["eval", "--blind"])
    assert result.exit_code == 2
    assert "experiment ID" in result.output
