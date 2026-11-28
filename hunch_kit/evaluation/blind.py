"""Local artifact previews for the blind ballot."""

from __future__ import annotations

import base64
import hashlib
import stat
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps


Kind = Literal["image", "text"]
Fingerprint = tuple[Kind, bytes]


@dataclass(frozen=True)
class Preview:
    kind: Kind
    content: str


def _png_preview(raw: bytes, mask: list[int] | None) -> str:
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
            if mask is not None:
                left, top, right, bottom = mask
                if not (0 <= left < right <= pixels.width and 0 <= top < bottom <= pixels.height):
                    raise ValueError("The blind image mask is outside the preview.")
                pixels.paste((240, 240, 240, 255), (left, top, right, bottom))
    except Exception as exc:
        # Pillow plugins use different exceptions for invalid headers and pixel data.
        raise ValueError("The image could not be decoded as a still preview.") from exc
    pixels.info.clear()
    encoded = BytesIO()
    pixels.save(encoded, format="PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(encoded.getbuffer()).decode("ascii")


def _reject_remote(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("//") or "://" in normalized or normalized.lower().startswith("file:"):
        raise ValueError("A local artifact is required.")


def local_file(directory: Path, name: str) -> Path:
    """Walk from the trusted local directory, checking links before traversing them."""
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

    def __init__(self, image_masks: dict[str, list[int]] | None = None) -> None:
        self.image_masks = image_masks or {}
        self.recent: OrderedDict[Fingerprint, Preview] = OrderedDict()

    def read(self, directory: Path, output_path: str) -> Fingerprint:
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
        key = (kind, hashlib.sha256(raw).digest())
        if key in self.recent:
            self.recent.move_to_end(key)
        else:
            if kind == "image":
                content = _png_preview(raw, self.image_masks.get(key[1].hex()))
            else:
                content = raw.decode("utf-8")
            self.recent[key] = Preview(kind, content)
            if len(self.recent) > 4:  # Two full A/B pairs. Completed votes retain their mapping.
                self.recent.popitem(last=False)
        return key
