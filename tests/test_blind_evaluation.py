"""Offline reviews of the checked-in design thinking outputs."""

import asyncio
import base64
import hashlib
import os
import re
import secrets
import shutil
import stat
import struct
import subprocess
import threading
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from click.testing import CliRunner
from PIL import Image, PngImagePlugin

from hunch_kit import manifest as manifest_io
from hunch_kit import runner
from hunch_kit.cli import cli
from hunch_kit.evaluation.blind import BlindPreviews
from hunch_kit.evaluation.human import create_app
from hunch_kit.providers.base import BaseProvider, ProviderResult


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
                # The provider signature occupies the last 208 x 32 pixels.
                corner = (1328, 2720, 1536, 2752)
                colors = actual.crop(corner).convert("RGBA").getcolors(1)
                assert colors and colors[0][1][3] == 255
                assert expected.crop(corner).convert("RGB").getcolors(1) is None
                for box in [(0, 0, 1536, 2720), (0, 2720, 1328, 2752)]:
                    source_pixels = expected.crop(box).convert("RGBA").tobytes()
                    assert actual.crop(box).convert("RGBA").tobytes() == source_pixels
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
    for name, baseline, output in [("first", "", "Amber"), ("second", "first", "Blue")]:
        directory = tmp_project / "experiments" / name
        manifest_io.Manifest(id=name, baseline=baseline, output_path="result.txt").save(directory)
        (directory / "result.txt").write_text(output, encoding="utf-8")
    return tmp_project


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [None, "From stub_local: "])
async def test_bundled_provider_attribution_is_hidden(tmp_project, prefix):
    shutil.copytree(EXAMPLE.parent / "campaign/providers", tmp_project / "providers")
    for name, provider, baseline in [("first", "echo", ""), ("second", "stub_local", "first")]:
        directory = tmp_project / "experiments" / name
        config = {"prefix": prefix} if provider == "stub_local" and prefix else {}
        manifest_io.Manifest(id=name, baseline=baseline, provider=provider,
                             provider_config=config, input_path="input.txt").save(directory)
        (directory / "input.txt").write_text("Describe a sunset.", encoding="utf-8")
        assert runner.run_experiment(directory).status == "success"

    child_output = tmp_project / "experiments/second/output/result.txt"
    original = child_output.read_bytes()
    assert b"stub_local" in original
    async with client_for(tmp_project, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert "stub_local" not in page.text
        assert page.text.count("<pre>Describe a sunset.</pre>") == 2
        assert (await client.post(ReviewForm(page.text).votes["Tie"])).status_code == 200
    assert child_output.read_bytes() == original


@pytest.mark.asyncio
async def test_unrecognized_stub_attribution_withholds_ballot(text_pair):
    directory = text_pair / "experiments/second"
    manifest = manifest_io.Manifest.load(directory)
    manifest.provider = "stub_local"
    manifest.save(directory)
    (directory / "result.txt").write_text("Earlier stub_local export: The proposal.", encoding="utf-8")
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert page.status_code == 409
        assert "stub_local" not in page.text and not ReviewForm(page.text).votes


@pytest.mark.asyncio
@pytest.mark.parametrize("label", [
    "SECOND", "first", "A simpler layout reads faster", "baseline_engine",
    '"clarity": 7.25', "prior detail score of 8", "overall_preference: better",
    "Previous score was 7.25", "Clarity (previous): 7.25",
])
async def test_text_ballot_checks_both_manifests(text_pair, label):
    experiments = text_pair / "experiments"
    baseline = manifest_io.Manifest.load(experiments / "first")
    baseline.hypothesis = "A simpler layout reads faster"
    baseline.provider = "baseline_engine"
    baseline.human_scores = {"clarity": 7.25}
    baseline.save(experiments / "first")
    child = manifest_io.Manifest.load(experiments / "second")
    child.provider = "echo"
    child.automated_scores = {"detail": 8}
    child.output_path = "result.yaml"
    child.save(experiments / "second")
    output = experiments / "second/result.yaml"
    output.write_text("[echo] A simple design", encoding="utf-8")
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        ballot = await client.get("/")
        action = ReviewForm(ballot.text).votes["Tie"]
        output.write_text("[echo] " + label, encoding="utf-8")
        fresh = await client.get("/")
        assert fresh.status_code == 409
        assert label not in fresh.text and not ReviewForm(fresh.text).votes
        assert (await client.post(action)).status_code == 409


@pytest.mark.asyncio
async def test_new_notebooklm_child_requires_verified_pixels(campaign):
    experiments = campaign / "experiments"
    directory = shutil.copytree(experiments / "dt_v3", experiments / "dt_v4")
    child = manifest_io.Manifest.load(directory)
    child.id, child.baseline = "dt_v4", "dt_v3"
    child.save(directory)
    image = directory / child.output_path
    async with client_for(campaign, filter_id="dt_v4", blind=True) as client:
        identical = await client.get("/")
        assert identical.status_code == 200
        with Image.open(image) as original:
            pixels = original.convert("RGBA")
        pixels.putpixel((0, 0), (255, 0, 255, 255))
        pixels.save(image, compress_level=1)
        fresh = await client.get("/")
        assert fresh.status_code == 409 and not ReviewForm(fresh.text).images
        assert (await client.post(ReviewForm(identical.text).votes["A"])).status_code == 409


def test_verified_clean_image_requires_an_exact_pixel_match(tmp_path):
    path = tmp_path / "clean.png"
    Image.new("RGBA", (1, 1), "blue").save(path)
    digest = hashlib.sha256(struct.pack(">II4B", 1, 1, 0, 0, 255, 255)).hexdigest()
    previews = BlindPreviews({tmp_path.name: {"pixel_sha256": digest, "rectangle": None}})
    key = previews.read(tmp_path, path.name, declared_provider="NotebookLM")
    encoded = previews.recent[key].content.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as preview:
        assert preview.getpixel((0, 0)) == (0, 0, 255, 255)
    Image.new("RGBA", (1, 1), "red").save(path)
    with pytest.raises(ValueError, match="verified"):
        previews.read(tmp_path, path.name, declared_provider="NotebookLM")


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["pixel_sha256", "rectangle"])
async def test_invalid_mask_is_local_to_affected_comparison(campaign, missing):
    config = campaign / "hunch_project.yaml"
    data = yaml.safe_load(config.read_bytes())
    del data["blind_image_masks"]["dt_v3"][missing]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    async with client_for(campaign, filter_id="dt_v1") as client:
        root = await client.get("/")
        assert root.status_code == 200 and 'id="scoring-form"' in root.text
        assert (await client.get("/blind?experiment_id=dt_v2")).status_code == 200
        unavailable = await client.get("/blind?experiment_id=dt_v3")
        assert unavailable.status_code == 409 and "mask" in unavailable.text.lower()
        assert "dt_v3" not in unavailable.text and not ReviewForm(unavailable.text).votes


@pytest.mark.asyncio
async def test_reexport_retains_mask_and_changed_pixels_require_review(campaign, monkeypatch):
    monkeypatch.setattr(secrets, "choice", lambda slots: "A")
    path = campaign / "experiments/dt_v3/output/result.png"
    with Image.open(path) as original:
        pixels = original.copy()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Export", "Another lossless encoding")
    pixels.save(path, pnginfo=metadata, compress_level=1)
    assert path.read_bytes() != (EXAMPLE / "experiments/dt_v3/output/result.png").read_bytes()
    async with client_for(campaign, filter_id="dt_v3", blind=True) as client:
        page = await client.get("/")
        assert page.status_code == 200
        form = ReviewForm(page.text)
        encoded = form.images["Output A"].split(",", 1)[1]
        with Image.open(BytesIO(base64.b64decode(encoded))) as preview:
            assert preview.crop((1328, 2720, 1536, 2752)).getcolors(1) == [(208 * 32, (240, 240, 240, 255))]
        orientation = Image.Exif()
        orientation[274] = 3
        pixels.save(path, exif=orientation, compress_level=1)
        rotated = await client.get("/")
        assert rotated.status_code == 409 and not ReviewForm(rotated.text).votes
        pixels.putpixel((0, 0), (255, 0, 255, 255))
        pixels.save(path, compress_level=1)
        fresh = await client.get("/")
        assert fresh.status_code == 409
        assert not ReviewForm(fresh.text).votes
        assert (await client.post(form.votes["A"])).status_code == 409


@pytest.mark.asyncio
async def test_older_ballot_keeps_its_original_preference(text_pair):
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        older = await client.get("/")
        newer = await client.get("/")
        assert (await client.post(ReviewForm(newer.text).votes["Tie"])).status_code == 200
        await client.get(older.url)
        rejected = await client.post(ReviewForm(older.text).votes["A"])
        assert rejected.status_code == 409
        assert "second" not in rejected.text
        assert manifest_io.Manifest.load(text_pair / "experiments/second").overall_preference == "equivalent"


@pytest.mark.asyncio
@pytest.mark.parametrize("new_votes", [["Tie", "A"], ["A"]])
async def test_old_ballot_expires_even_when_preference_is_unchanged(text_pair, monkeypatch, new_votes):
    monkeypatch.setattr(secrets, "choice", lambda slots: "A")
    directory = text_pair / "experiments/second"
    manifest_io.save_overall_preference(directory, "better")
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        old = await client.get("/")
        for vote in new_votes:
            # A separate evaluation server shares the campaign.
            async with client_for(text_pair, filter_id="second", blind=True) as other:
                new = await other.get("/")
                assert (await other.post(ReviewForm(new.text).votes[vote])).status_code == 200
        assert (await client.post(ReviewForm(old.text).votes["B"])).status_code == 409
        assert manifest_io.Manifest.load(directory).overall_preference == "better"


@pytest.mark.asyncio
@pytest.mark.parametrize(("update", "vote_status"), [
    ({"overall_preference": "better"}, 409), ({"notes": "Revised note"}, 200),
])
async def test_scoring_judgment_expires_ballot_and_notes_do_not(text_pair, update, vote_status):
    directory = text_pair / "experiments/second"
    manifest_io.save_overall_preference(directory, "better")
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert (await client.post("/eval/second/score", json=update)).status_code == 200
        assert (await client.post(ReviewForm(page.text).votes["Tie"])).status_code == vote_status


@pytest.mark.asyncio
async def test_baseline_edit_between_validation_and_commit(text_pair, monkeypatch):
    baseline_dir = text_pair / "experiments/first"
    (baseline_dir / "new.txt").write_text("Different design", encoding="utf-8")
    serialize = manifest_io._manifest_yaml

    def edit_baseline(data):
        if data.get("overall_preference") == "equivalent":
            baseline = manifest_io.Manifest.load(baseline_dir)
            baseline.output_path = "new.txt"
            baseline.save(baseline_dir)
        return serialize(data)

    async with client_for(text_pair, filter_id="second", blind=True) as client:
        original = await client.get("/")
        monkeypatch.setattr(manifest_io, "_manifest_yaml", edit_baseline)
        assert (await client.post(ReviewForm(original.text).votes["Tie"])).status_code == 409
        assert not manifest_io.Manifest.load(text_pair / "experiments/second").overall_preference


@pytest.mark.asyncio
@pytest.mark.parametrize(("experiment", "status"), [("first", "failed"), ("second", "timeout")])
async def test_unsuccessful_rerun_withholds_retained_output(text_pair, monkeypatch, experiment, status):
    directory = text_pair / "experiments" / experiment
    manifest = manifest_io.Manifest.load(directory)
    manifest.input_path = "result.txt"
    manifest.provider = "echo"
    manifest.save(directory)
    assert runner.run_experiment(directory).status == "success"

    class Unsuccessful(BaseProvider):
        def run(self, input_text, config=None):
            return ProviderResult(output="", status=status)

    async with client_for(text_pair, filter_id="second", blind=True) as client:
        ballot = await client.get("/")
        action = ReviewForm(ballot.text).votes["A"]
        monkeypatch.setattr(runner, "resolve_provider", lambda *a, **kw: Unsuccessful())
        assert runner.run_experiment(directory).status == status
        assert (await client.get("/")).status_code == 409
        assert (await client.post(action)).status_code == 409
        assert not manifest_io.Manifest.load(text_pair / "experiments/second").overall_preference


@pytest.mark.asyncio
async def test_revisionless_root_scoring_merges_supplied_fields(text_pair):
    path = text_pair / "experiments/first/experiment.yaml"
    data = yaml.safe_load(path.read_bytes())
    data.update(human_scores={"clarity": 6}, notes="Keep this note", campaign_annotation="Keep this field")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    async with client_for(text_pair) as client:
        saved = await client.post("/eval/first/score", json={"scores": {"quality": "8"}})
        assert saved.status_code == 200
        assert saved.json()["revision"] == manifest_io.Manifest.load(path).revision_token
    assert yaml.safe_load(path.read_bytes()) == {**data, "human_scores": {"clarity": 6, "quality": 8.0}}


@pytest.mark.asyncio
async def test_vote_accepts_read_only_baseline(text_pair):
    baseline = text_pair / "experiments/first"
    before = {p.name: p.read_bytes() for p in baseline.iterdir()}
    if os.name == "nt":
        # A Windows directory's read-only attribute does not restrict file creation.
        subprocess.run(["icacls", str(baseline), "/deny", "*S-1-1-0:(OI)(CI)(WD,AD)"],
                       check=True, capture_output=True)
    else:
        baseline.chmod(0o555)
        for path in baseline.iterdir():
            path.chmod(0o444)
    try:
        async with client_for(text_pair, filter_id="second", blind=True) as client:
            ballot = await client.get("/")
            saved = await client.post(ReviewForm(ballot.text).votes["Tie"])
            assert saved.status_code == 200
        assert {p.name: p.read_bytes() for p in baseline.iterdir()} == before
    finally:
        if os.name == "nt":
            subprocess.run(["icacls", str(baseline), "/remove:d", "*S-1-1-0"], check=True, capture_output=True)
        else:
            baseline.chmod(0o755)
            for path in baseline.iterdir():
                path.chmod(0o644)


def test_manifest_and_run_locks_leave_campaign_files_untouched(text_pair):
    directory = text_pair / "experiments/second"
    legacy_paths = [directory / ".experiment.yaml.lock", directory / ".experiment.run.lock"]
    for path in legacy_paths:
        path.write_bytes(b"campaign data")
    runner.run_experiment(directory, provider_override="echo")
    assert all(path.is_file() and path.read_bytes() == b"campaign data" for path in legacy_paths)


@pytest.mark.asyncio
async def test_review_shows_and_checks_text_endings(text_pair):
    experiments = text_pair / "experiments"
    prefix = "x" * 50_000
    for name, ending in [("first", "AMBER"), ("second", "BLUE")]:
        (experiments / name / "result.txt").write_text(prefix + ending, encoding="utf-8")
    manifest = experiments / "second" / "experiment.yaml"
    before = manifest.read_bytes()
    async with client_for(text_pair, filter_id="second", blind=True) as client:
        page = await client.get("/")
        assert prefix + "AMBER" in page.text and prefix + "BLUE" in page.text
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
        assert "<pre>Blue</pre>" in result.text
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
@pytest.mark.parametrize("problem", ["root", "missing baseline", "self baseline", "missing output", "null", "binary"])
async def test_unavailable_comparison_preserves_manifests(campaign, problem):
    experiments = campaign / "experiments"
    selected = "dt_v1" if problem == "root" else "dt_v3"
    path = experiments / selected / "experiment.yaml"
    data = yaml.safe_load(path.read_bytes())
    if problem == "missing baseline":
        data["baseline"] = "missing"
    if problem == "self baseline":
        data["baseline"] = selected
    if problem == "null":
        data["output_path"] = None
    if problem in {"missing output", "binary"}:
        data["output_path"] = "identifying-filename.bin"
        if problem == "binary":
            (path.parent / data["output_path"]).write_bytes(b"binary")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    async with client_for(campaign, filter_id=selected, blind=True) as client:
        page = await client.get("/")
        assert page.is_client_error
        if problem == "null":
            assert page.status_code == 409
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


@pytest.mark.asyncio
async def test_score_from_older_tab_preserves_blind_tie(campaign):
    async with client_for(campaign) as client:
        scoring = await client.get("/eval/dt_v3")
        assert '<option value="better" selected>' in scoring.text
        revision = re.search(r'data-revision="([^"]+)"', scoring.text)[1]
        ballot = await client.get("/blind?experiment_id=dt_v3")
        assert (await client.post(ReviewForm(ballot.text).votes["Tie"])).status_code == 200
        payload = {"revision": revision, "overall_preference": "better", "notes": "From the old scoring tab"}
        url = "/eval/dt_v3/score"
        assert (await client.post(url, json=payload)).status_code == 409
        child = campaign / "experiments/dt_v3"
        assert manifest_io.Manifest.load(child).overall_preference == "equivalent"

        fresh = await client.get("/eval/dt_v3")
        payload.update(revision=re.search(r'data-revision="([^"]+)"', fresh.text)[1],
                       overall_preference="equivalent")
        saved = await client.post(url, json=payload)
        assert saved.status_code == 200
        payload.update(revision=saved.json()["revision"], notes="A second edit")
        assert (await client.post(url, json=payload)).status_code == 200
        assert manifest_io.Manifest.load(child).notes == "A second edit"


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["sync", "async"])
@pytest.mark.parametrize("experiment", ["first", "second"])
async def test_ballot_during_rerun(text_pair, monkeypatch, entry, experiment):
    started, finish = threading.Event(), threading.Event()

    class PausedProvider(BaseProvider):
        def run(self, input_text, config=None):
            started.set()
            assert finish.wait(10)
            return ProviderResult(output="New output")

    directory = text_pair / "experiments" / experiment
    monkeypatch.setattr(runner, "resolve_provider", lambda *a, **kw: PausedProvider())

    def rerun():
        if entry == "sync":
            return runner.run_experiment(directory, provider_override="paused")
        return asyncio.run(runner.run_experiment_async(directory, provider_override="paused"))

    async with client_for(text_pair, filter_id="second", blind=True) as client:
        ballot = await client.get("/")
        action = ReviewForm(ballot.text).votes["Tie"]
        task = asyncio.create_task(asyncio.to_thread(rerun))
        try:
            assert await asyncio.to_thread(started.wait, 10)
            assert (await client.post(action)).status_code == 409
            assert (await client.get(ballot.url)).status_code == 409
        finally:
            finish.set()
            await task
        assert (await client.post(action)).status_code == 409
        saved = manifest_io.Manifest.load(directory)
        assert saved.status == "success"
        assert (directory / saved.output_path).read_text() == "New output"
        assert not manifest_io.Manifest.load(text_pair / "experiments/second").overall_preference


def test_grayscale_ramp_retains_tones(tmp_path):
    samples = (0, 1024, 32768, 60000, 65535)
    path = tmp_path / "ramp.png"
    Image.frombytes("I;16", (5, 1), struct.pack("<5H", *samples)).save(path)
    previews = BlindPreviews()
    fingerprint = previews.read(tmp_path, path.name)
    encoded = previews.recent[fingerprint].content.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as preview:
        assert list(preview.convert("L").getdata()) == [0, 4, 128, 233, 255]


def test_png_preview_preserves_color_fields(tmp_path):
    path = tmp_path / "linear.png"
    info = PngImagePlugin.PngInfo()
    info.add(b"gAMA", struct.pack(">I", 100000))
    info.add(b"cHRM", struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000))
    info.add_text("Author", "Identifying name")
    Image.new("RGB", (1, 1), (128, 128, 128)).save(path, pnginfo=info)
    previews = BlindPreviews()
    key = previews.read(tmp_path, path.name)
    encoded = previews.recent[key].content.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as preview, Image.open(path) as source:
        assert preview.info == {key: source.info[key] for key in ("gamma", "chromaticity")}
        assert preview.convert("RGB").getpixel((0, 0)) == (128, 128, 128)


def test_cmyk_preview_applies_embedded_profile():
    # The synthetic ICC profile maps this CMYK swatch to neutral gray (Lab L*=50).
    swatch = Path(__file__).parent / "fixtures/cmyk-neutral.jpg"
    previews = BlindPreviews()
    key = previews.read(swatch.parent, swatch.name)
    encoded = previews.recent[key].content.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as preview:
        assert preview.info == {}
        assert preview.getpixel((0, 0)) == (119, 119, 119, 255)


def test_legacy_encoded_image_masks_require_a_match(tmp_path):
    path = tmp_path / "output.png"
    Image.new("RGB", (2, 1), "blue").save(path)
    previews = BlindPreviews({hashlib.sha256(path.read_bytes()).hexdigest(): [0, 0, 1, 1]})
    key = previews.read(tmp_path, path.name)
    encoded = previews.recent[key].content.split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as preview:
        assert list(preview.getdata()) == [(240, 240, 240, 255), (0, 0, 255, 255)]
    Image.new("RGB", (2, 1), "red").save(path)
    with pytest.raises(ValueError):
        previews.read(tmp_path, path.name)


@pytest.mark.parametrize("remote", [r"\\server\share\plot.png", "//server/share/plot.png",
                                    r"\\?\UNC\server\share\plot.png", "smb://server/share/plot.png"])
def test_remote_preview_is_rejected_before_filesystem_access(tmp_path, monkeypatch, remote):
    def unexpected_io(*args, **kwargs):
        pytest.fail("Remote output reached the filesystem")

    with monkeypatch.context() as patch:
        for method in ("resolve", "lstat", "stat", "read_bytes"):
            patch.setattr(Path, method, unexpected_io)
        with pytest.raises(ValueError):
            BlindPreviews().read(tmp_path, remote)


@pytest.mark.parametrize("link_mode", [stat.S_IFLNK, stat.S_IFDIR])
def test_preview_checks_redirect_before_following_it(tmp_path, monkeypatch, link_mode):
    redirect = tmp_path / "redirect"

    def inspect(path):
        assert path == redirect
        return SimpleNamespace(st_mode=link_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", inspect)
        patch.setattr(Path, "readlink", lambda path: Path(r"\\server\share"))
        for method in ("stat", "resolve", "read_bytes"):
            patch.setattr(Path, method, lambda *a, **kw: pytest.fail("Followed a remote redirect"))
        with pytest.raises(ValueError):
            BlindPreviews().read(tmp_path, "redirect/result.png")


def test_local_redirect_uses_the_declared_output_type(tmp_path, monkeypatch):
    alias = tmp_path / "result.txt"
    (tmp_path / "artifact").write_text("Local result", encoding="utf-8")
    lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda p: SimpleNamespace(st_mode=stat.S_IFLNK) if p == alias else lstat(p))
    monkeypatch.setattr(Path, "readlink", lambda p: Path("artifact"))
    previews = BlindPreviews()
    key = previews.read(tmp_path, alias.name)
    assert previews.recent[key].content == "Local result"
