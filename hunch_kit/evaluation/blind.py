"""Local artifact previews for the blind ballot."""

from __future__ import annotations

import base64
import hashlib
import re
import stat
import struct
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageCms, ImageOps, PngImagePlugin

from ..manifest import Manifest
from ..project import ImageMask


Kind = Literal["image", "text"]
Fingerprint = tuple[Kind, bytes, tuple[str, ...]]


class UnverifiedAttribution(ValueError):
    """A preview cannot safely hide its source. Messages may appear on the ballot."""


@dataclass(frozen=True)
class Preview:
    kind: Kind
    content: str


def _decode_pixels(raw: bytes) -> tuple[Image.Image, PngImagePlugin.PngInfo]:
    formats = ("PNG", "JPEG", "GIF", "WEBP")
    try:
        with Image.open(BytesIO(raw), formats=formats) as image:
            image.verify()
        with Image.open(BytesIO(raw), formats=formats) as image:
            if getattr(image, "is_animated", False):
                raise ValueError("A still image is required.")
            oriented = ImageOps.exif_transpose(image)
            if image.format == "PNG" and (oriented.mode == "I" or oriented.mode.startswith("I;16")):
                # Pillow exposes 16-bit grayscale PNGs as I or I;16 depending on version.
                gray = oriented.convert("I")
                pixels = gray.point(lambda sample: (sample + 128) * (1 / 257)).convert("RGBA")
                if "transparency" in image.info:
                    transparent = image.info["transparency"]
                    alpha = bytes(0 if v == transparent else 255 for v in gray.getdata())
                    pixels.putalpha(Image.frombytes("L", gray.size, alpha))
            else:
                pixels = oriented.convert("RGBA")
            color_chunks = PngImagePlugin.PngInfo()
            if profile := image.info.get("icc_profile"):
                if oriented.mode == "CMYK":
                    color = oriented
                elif oriented.mode in ("1", "L", "LA", "I") or oriented.mode.startswith("I;16"):
                    color = pixels.getchannel("R")
                else:
                    color = pixels.convert("RGB")
                srgb = ImageCms.profileToProfile(
                    color, BytesIO(profile), ImageCms.createProfile("sRGB"), outputMode="RGB",
                )
                srgb.putalpha(pixels.getchannel("A"))
                pixels = srgb
            elif image.format == "PNG":
                if "gamma" in image.info:
                    color_chunks.add(b"gAMA", struct.pack(">I", round(image.info["gamma"] * 100000)))
                if "chromaticity" in image.info:
                    values = (round(v * 100000) for v in image.info["chromaticity"])
                    color_chunks.add(b"cHRM", struct.pack(">8I", *values))
                if "srgb" in image.info:
                    color_chunks.add(b"sRGB", struct.pack("B", image.info["srgb"]))
            return pixels, color_chunks
    except Exception as exc:
        # Pillow plugins use different exceptions for invalid headers and pixel data.
        raise ValueError("The image could not be decoded as a still preview.") from exc


def _pixel_digest(pixels: Image.Image) -> str:
    digest = hashlib.sha256(struct.pack(">II", *pixels.size))
    digest.update(pixels.tobytes())
    return digest.hexdigest()


def _png_preview(pixels: Image.Image, mask: list[int] | None, color_chunks: PngImagePlugin.PngInfo) -> str:
    if mask is not None:
        left, top, right, bottom = mask
        if not (0 <= left < right <= pixels.width and 0 <= top < bottom <= pixels.height):
            raise UnverifiedAttribution("The blind image mask is outside the preview.")
        pixels.paste((240, 240, 240, 255), (left, top, right, bottom))
    pixels.info.clear()
    encoded = BytesIO()
    pixels.save(encoded, format="PNG", compress_level=1, pnginfo=color_chunks)
    return "data:image/png;base64," + base64.b64encode(encoded.getbuffer()).decode("ascii")


def identifying_patterns(*manifests: Manifest) -> tuple[str, ...]:
    """Match explicit identity and score labels belonging to either output."""
    metadata_field = (r"provider|hypothesis|experiment[ _-]?id|baseline|"
                      r"(?:human|automated)[ _-]scores|overall[ _-]preference|scores?")
    patterns = {
        rf"""\b(?:{metadata_field})["']?\s*[:=]""",
        r"\b(?:score|rating)\s+(?:(?:is|was|of)\s+)?[-+]?\d",
    }
    for manifest in manifests:
        labels = (manifest.id, manifest.hypothesis, manifest.provider,
                  (manifest.provider_metadata or {}).get("provider"))
        for label in labels:
            if isinstance(label, str) and label.strip():
                words = r"\s+".join(re.escape(word) for word in label.split())
                patterns.add(rf"(?:^|[\W_]){words}(?=$|[\W_])")
        for scores in (manifest.human_scores, manifest.automated_scores):
            for dimension, value in (scores or {}).items():
                words = r"[\s_-]+".join(re.escape(word) for word in re.split(r"[\s_-]+", str(dimension)))
                score_label = r"(?:score|rating)\b|[:=]|(?:(?:was|is|of)\s+)?[-+]?\d"
                patterns.add(rf"""\b{words}\b["']?\s*(?:{score_label})""")
                if isinstance(value, (float, int, str)) and str(value):
                    rating = re.escape(str(value))
                    # Check each line once for the dimension and its recorded value in either order.
                    patterns.add(rf"(?m)^(?=[^\n]*\b{words}\b)(?=[^\n]*(?<!\w){rating}(?!\w))")
    return tuple(sorted(patterns))


def _text_preview(raw: bytes, provider: str, prefix: str, patterns: tuple[str, ...]) -> str:
    text = raw.decode("utf-8").removeprefix(prefix)
    if provider:
        patterns = (*patterns, rf"(?<!\w){re.escape(provider)}(?!\w)")
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
        raise UnverifiedAttribution("The text contains identifying information or score labels.")
    return text


def _reject_remote(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("//") or "://" in normalized or normalized.lower().startswith("file:"):
        raise ValueError("A local artifact is required.")


def local_file(directory: Path, name: str) -> Path:
    """Walk from the trusted local directory, checking links before traversing them."""
    if not isinstance(name, str) or not name:
        raise ValueError("A local filename is required.")
    _reject_remote(name)
    _reject_remote(str(directory))
    root = directory.absolute()
    pending = list(reversed((root / name).relative_to(root).parts))
    current = root
    redirects = 0
    while pending:
        part = pending.pop()
        if part == "..":
            if current == root:
                raise ValueError("The artifact is outside the experiment directory.")
            current = current.parent
            continue
        candidate = current / part
        if not candidate.is_relative_to(root):
            raise ValueError("The artifact is outside the experiment directory.")
        info = candidate.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            redirects += 1
            if redirects > 40:
                raise ValueError("Too many artifact redirects.")
            target = str(candidate.readlink())
            # Windows readlink can return an extended drive-letter path for a local junction.
            if target.startswith("\\\\?\\") and target[5:7] == ":\\":
                target = target[4:]
            _reject_remote(target)
            parts = (current / target).relative_to(root).parts
            pending.extend(reversed(parts))
            current = root
        else:
            current = candidate
    if not current.is_file():
        raise ValueError("A local file is required.")
    return current


def _rectangle(value: object) -> list[int] | None:
    if value is None:  # Explicitly verified clean at the configured pixel digest.
        return None
    if not isinstance(value, list) or len(value) != 4 or any(type(v) is not int for v in value):
        raise UnverifiedAttribution("The blind image mask needs four integer coordinates.")
    return value


class BlindPreviews:
    """Share recent previews across refreshes and votes without retaining every ballot."""

    def __init__(self, image_masks: dict[str, ImageMask | list[int]] | None = None) -> None:
        self.image_masks = image_masks if image_masks is not None else {}
        self.recent: OrderedDict[Fingerprint, Preview] = OrderedDict()

    def _image_mask(self, name: str, pixels: Image.Image, raw_hash: bytes, required: bool) -> list[int] | None:
        digest = _pixel_digest(pixels) if self.image_masks else ""
        if name in self.image_masks:
            rule = self.image_masks[name]
            if not isinstance(rule, dict) or "rectangle" not in rule or not re.fullmatch(
                r"[0-9a-f]{64}", str(rule.get("pixel_sha256", "")),
            ):
                raise UnverifiedAttribution("The blind image mask configuration is incomplete.")
            if rule["pixel_sha256"] != digest:
                raise UnverifiedAttribution("An image has changed since its mask was verified.")
            return _rectangle(rule["rectangle"])

        # Lossless copies can reuse a verified pixel rule under a different ID.
        for rule in self.image_masks.values():
            if isinstance(rule, dict) and rule.get("pixel_sha256") == digest and "rectangle" in rule:
                return _rectangle(rule["rectangle"])
        legacy = self.image_masks.get(raw_hash.hex())
        if isinstance(legacy, list):
            return _rectangle(legacy)
        if required or any(re.fullmatch(r"[0-9a-f]{64}", str(key)) for key in self.image_masks):
            raise UnverifiedAttribution("A verified image mask is required.")
        return None

    def read(
        self, directory: Path, output_path: str, *, attribution: tuple[str, str] = ("", ""),
        patterns: tuple[str, ...] = (), declared_provider: str = "",
    ) -> Fingerprint:
        path = local_file(directory, output_path)
        suffix = Path(output_path).suffix.lower()
        kind: Kind
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            kind = "image"
        elif suffix in (".txt", ".md", ".yaml", ".yml", ".json"):
            kind = "text"
        else:
            raise ValueError("Unsupported artifact type.")

        raw = path.read_bytes()
        if not raw:
            raise ValueError("Empty artifact.")
        if not isinstance(self.image_masks, dict):
            raise UnverifiedAttribution("The blind image mask configuration must be a mapping.")
        if directory.name in self.image_masks and kind != "image":
            raise UnverifiedAttribution("The designated output requires a verified image mask.")
        raw_hash = hashlib.sha256(raw).digest()
        # A cached preview must satisfy the same mask or attribution requirements.
        require_verified_image = "notebooklm" in {str(declared_provider).strip().casefold(),
                                                  attribution[0].strip().casefold()}
        policy = (directory.name, str(require_verified_image)) if kind == "image" else (*attribution, *patterns)
        key = (kind, raw_hash, policy)
        if key in self.recent:
            self.recent.move_to_end(key)
        else:
            if kind == "image":
                pixels, color_chunks = _decode_pixels(raw)
                mask = self._image_mask(directory.name, pixels, raw_hash, require_verified_image)
                content = _png_preview(pixels, mask, color_chunks)
            else:
                content = _text_preview(raw, *attribution, patterns)
            self.recent[key] = Preview(kind, content)
            if len(self.recent) > 4:  # Two full A/B pairs. Completed votes retain their mapping.
                self.recent.popitem(last=False)
        return key
