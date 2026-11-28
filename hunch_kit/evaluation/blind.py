"""Local artifact previews for the blind ballot."""

from __future__ import annotations

import base64
import hashlib
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


def _png_preview(raw: bytes) -> str:
    formats = ("PNG", "JPEG", "GIF", "WEBP")
    try:
        with Image.open(BytesIO(raw), formats=formats) as image:
            image.verify()
        with Image.open(BytesIO(raw), formats=formats) as image:
            if getattr(image, "is_animated", False):
                raise ValueError("A still image is required.")
            pixels = ImageOps.exif_transpose(image).convert("RGBA")
    except Exception as exc:
        # Pillow plugins use different exceptions for invalid headers and pixel data.
        raise ValueError("The image could not be decoded as a still preview.") from exc
    pixels.info.clear()
    encoded = BytesIO()
    pixels.save(encoded, format="PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(encoded.getbuffer()).decode("ascii")


class BlindPreviews:
    """Share recent previews across refreshes and votes without retaining every ballot."""

    def __init__(self) -> None:
        self.recent: OrderedDict[Fingerprint, Preview] = OrderedDict()

    def read(self, directory: Path, output_path: str) -> Fingerprint:
        path = directory / output_path
        if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError("A local artifact is required.")
        kind: Kind
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            kind = "image"
        elif path.suffix.lower() in (".txt", ".md", ".yaml", ".yml", ".json"):
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
            content = _png_preview(raw) if kind == "image" else raw.decode("utf-8")
            self.recent[key] = Preview(kind, content)
            if len(self.recent) > 4:  # Two full A/B pairs. Completed votes retain their mapping.
                self.recent.popitem(last=False)
        return key
