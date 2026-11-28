"""Offline reviews of the checked-in design thinking outputs."""

import base64
import re
import secrets
import shutil
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import yaml
from click.testing import CliRunner
from PIL import Image

from hunch_kit import manifest as manifest_io
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
            source = experiments / exp_id / "output/result.png"
            original = EXAMPLE / "experiments" / exp_id / "output/result.png"
            preview = base64.b64decode(encoded, validate=True)
            assert b"notebooklm" not in preview.lower()
            with Image.open(BytesIO(preview)) as actual, Image.open(original) as expected:
                assert actual.info == {}
                assert actual.size == expected.size
                assert actual.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes()
                assert not actual.private_chunks
            assert source.read_bytes() == original.read_bytes()

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
        for name in ("dt_v2", "dt_v3"):
            image = Path(name) / "output/result.png"
            assert (experiments / image).read_bytes() == (EXAMPLE / "experiments" / image).read_bytes()


@pytest.fixture
def text_pair(tmp_project):
    for name, baseline in [("first", ""), ("second", "first")]:
        directory = tmp_project / "experiments" / name
        manifest_io.Manifest(id=name, baseline=baseline, output_path="result.txt").save(directory)
        (directory / "result.txt").write_text(name.upper(), encoding="utf-8")
    return tmp_project


@pytest.mark.asyncio
async def test_review_shows_and_checks_text_endings(text_pair):
    experiments = text_pair / "experiments"
    prefix = "x" * 50_000
    for name in ("first", "second"):
        (experiments / name / "result.txt").write_text(prefix + name.upper(), encoding="utf-8")
    manifest = experiments / "second" / "experiment.yaml"
    before = manifest.read_bytes()
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert prefix + "FIRST" in page.text and prefix + "SECOND" in page.text
        (experiments / "second" / "result.txt").write_text(prefix + "EDITED", encoding="utf-8")
        result = await client.post(ReviewForm(page.text).votes["Tie"])
        assert result.status_code == 409
        assert manifest.read_bytes() == before


@pytest.mark.asyncio
async def test_text_with_image_prefix_is_reviewable_text(text_pair):
    path = text_pair / "experiments/second/result.txt"
    path.write_text("data:image is <text>", encoding="utf-8")
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert "<pre>data:image is &lt;text&gt;</pre>" in page.text
        assert not ReviewForm(page.text).images


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["truncated", "over decoder limit"])
async def test_unreadable_png_cannot_be_reviewed_or_voted_on(campaign, monkeypatch, problem):
    child = campaign / "experiments/dt_v3/experiment.yaml"
    before = child.read_bytes()
    image = child.parent / "output/result.png"
    async with client_for(campaign, filter_id="dt_v3", blind=True) as client:
        page = await client.get("/")
        action = ReviewForm(page.text).votes["Tie"]
        if problem == "truncated":
            # Keep a valid header and cut through the compressed pixel data.
            image.write_bytes(image.read_bytes()[:image.stat().st_size // 2])
        else:
            Image.new("RGB", (20, 20)).save(image)
            monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
        fresh = await client.get("/")
        assert fresh.status_code == 409
        assert not ReviewForm(fresh.text).votes
        assert (await client.post(action)).status_code == 409
        assert child.read_bytes() == before


@pytest.mark.asyncio
async def test_reveal_survives_changed_output_after_submission(text_pair, monkeypatch):
    monkeypatch.setattr(secrets, "choice", lambda slots: "B")
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        saved = await client.post(ReviewForm(page.text).votes["Tie"], follow_redirects=False)
        assert saved.status_code == 303
        (text_pair / "experiments/second/result.txt").write_text("replacement", encoding="utf-8")
        result = await client.get(saved.headers["location"])
        assert result.status_code == 200
        assert "A = first (baseline)" in result.text and "B = second (child)" in result.text
        assert "equivalent" in result.text and "replacement" not in result.text
        assert "<pre>SECOND</pre>" in result.text
        assert not ReviewForm(result.text).votes


@pytest.mark.asyncio
async def test_concurrent_note_between_vote_read_and_replace(text_pair, monkeypatch):
    child = text_pair / "experiments/second/experiment.yaml"
    serialize = manifest_io._manifest_yaml

    def save_note_during_vote(data):
        if data.get("overall_preference") == "equivalent":
            note = manifest_io.Manifest.load(child)
            note.notes = "Saved during the vote"
            note.save(child.parent)
        return serialize(data)

    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        action = ReviewForm(page.text).votes["Tie"]
        with monkeypatch.context() as patch:
            patch.setattr(manifest_io, "_manifest_yaml", save_note_during_vote)
            result = await client.post(action)
        assert manifest_io.Manifest.load(child).notes == "Saved during the vote"
        if result.status_code == 409:
            assert not manifest_io.Manifest.load(child).overall_preference
            result = await client.post(action)
        assert result.status_code == 200
        saved = manifest_io.Manifest.load(child)
        assert saved.overall_preference == "equivalent" and saved.notes == "Saved during the vote"


@pytest.mark.asyncio
async def test_stale_manifest_writer_cannot_erase_vote(text_pair):
    directory = text_pair / "experiments/second"
    editing = manifest_io.Manifest.load(directory)
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert (await client.post(ReviewForm(page.text).votes["Tie"])).status_code == 200
    editing.notes = "Edited from the previous revision"
    with pytest.raises(manifest_io.ManifestConflict, match="changed"):
        editing.save(directory)
    assert manifest_io.Manifest.load(directory).overall_preference == "equivalent"


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

    async with client_for(campaign, filter_id="dt_v3", blind=True) as client:
        root = await client.get("/eval/dt_v1")
        assert root.status_code == 200 and 'id="scoring-form"' in root.text


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
