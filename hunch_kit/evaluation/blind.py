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

from PIL import Image, ImageCms, ImageOps

from ..project import ImageMask


Kind = Literal["image", "text"]
Fingerprint = tuple[Kind, bytes, tuple[str, ...]]


class UnverifiedAttribution(ValueError):
    """A preview cannot safely hide its source. Messages may appear on the ballot."""


@dataclass(frozen=True)
class Preview:
    kind: Kind
    content: str


def _decode_pixels(raw: bytes) -> Image.Image:
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
            return pixels
    except Exception as exc:
        # Pillow plugins use different exceptions for invalid headers and pixel data.
        raise ValueError("The image could not be decoded as a still preview.") from exc


def _pixel_digest(pixels: Image.Image) -> str:
    digest = hashlib.sha256(struct.pack(">II", *pixels.size))
    digest.update(pixels.tobytes())
    return digest.hexdigest()


def _png_preview(pixels: Image.Image, mask: list[int] | None) -> str:
    if mask is not None:
        left, top, right, bottom = mask
        if not (0 <= left < right <= pixels.width and 0 <= top < bottom <= pixels.height):
            raise UnverifiedAttribution("The blind image mask is outside the preview.")
        pixels.paste((240, 240, 240, 255), (left, top, right, bottom))
    pixels.info.clear()
    encoded = BytesIO()
    pixels.save(encoded, format="PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(encoded.getbuffer()).decode("ascii")


def _text_preview(raw: bytes, provider: str, prefix: str) -> str:
    text = raw.decode("utf-8").removeprefix(prefix)
    if provider and re.search(rf"(?<!\w){re.escape(provider)}(?!\w)", text, re.IGNORECASE):
        raise UnverifiedAttribution("The text still identifies its provider.")
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


class BlindPreviews:
    """Share recent previews across refreshes and votes without retaining every ballot."""

    def __init__(self, image_masks: dict[str, ImageMask | list[int]] | None = None) -> None:
        self.required_pixels: dict[str, str] = {}
        self.pixel_masks: dict[str, list[int]] = {}
        self.legacy_masks: dict[str, list[int]] = {}
        for name, mask in (image_masks or {}).items():
            if isinstance(mask, list):
                self.legacy_masks[name] = mask
            else:
                digest = mask["pixel_sha256"]
                self.required_pixels[name] = digest
                self.pixel_masks[digest] = mask["rectangle"]
        self.recent: OrderedDict[Fingerprint, Preview] = OrderedDict()

    def _image_preview(self, raw: bytes, raw_hash: bytes, required: str | None) -> str:
        pixels = _decode_pixels(raw)
        mask = self.legacy_masks.get(raw_hash.hex())
        if self.pixel_masks:
            digest = _pixel_digest(pixels)
            if required is not None and digest != required:
                raise UnverifiedAttribution("An image has changed since its mask was verified.")
            mask = self.pixel_masks.get(digest, mask)
        if (required is not None or self.legacy_masks) and mask is None:
            # Encoded-file rules do not identify which experiment a miss belongs to.
            raise UnverifiedAttribution("A verified image mask is required.")
        return _png_preview(pixels, mask)

    def read(
        self, directory: Path, output_path: str, *, attribution: tuple[str, str] = ("", ""),
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
        required = self.required_pixels.get(directory.name)
        if required is not None and kind != "image":
            raise UnverifiedAttribution("The designated output requires a verified image mask.")
        raw_hash = hashlib.sha256(raw).digest()
        # A cached preview must satisfy the same mask or attribution requirements.
        key = (kind, raw_hash, (required or "",) if kind == "image" else attribution)
        if key in self.recent:
            self.recent.move_to_end(key)
        else:
            if kind == "image":
                content = self._image_preview(raw, raw_hash, required)
            else:
                content = _text_preview(raw, *attribution)
            self.recent[key] = Preview(kind, content)
            if len(self.recent) > 4:  # Two full A/B pairs. Completed votes retain their mapping.
                self.recent.popitem(last=False)
        return key
