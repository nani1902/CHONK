"""Shared synthetic fixtures. No real documents are used or committed."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Callable

import pytest
from PIL import Image
from pypdf import PdfWriter

from chonk.models import Profile


def make_image_pdf(path: Path, *, pages: int = 1, size=(300, 400), seed: int = 0) -> Path:
    """Write an image-only PDF of deterministic noisy gradients."""
    rng = random.Random(seed)
    images = []
    for page in range(pages):
        image = Image.new("RGB", size, "white")
        pixels = image.load()
        for y in range(size[1]):
            for x in range(size[0]):
                value = (x * y + page * 97 + rng.randint(0, 60)) % 256
                pixels[x, y] = (value, 255 - value, (value * 3) % 256)
        images.append(image)
    images[0].save(path, save_all=True, append_images=images[1:], resolution=150)
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PaddingBackend:
    """Test backend: copies the source and pads its metadata so output size is
    a controlled function of the profile. Rendering is unchanged."""

    def __init__(self, padding_for: Callable[[Profile], int]):
        self.padding_for = padding_for
        self.calls: list[Profile] = []

    def compress(self, source: Path, output: Path, profile: Profile, *, timeout: int) -> None:
        self.calls.append(profile)
        writer = PdfWriter(clone_from=str(source))
        writer.add_metadata({"/ChonkTestPadding": "x" * self.padding_for(profile)})
        with output.open("wb") as stream:
            writer.write(stream)


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    return make_image_pdf(tmp_path / "source.pdf", pages=2)


@pytest.fixture
def unpadded_size(image_pdf: Path, tmp_path: Path) -> int:
    """Size of a PaddingBackend output with zero padding."""
    probe = tmp_path / "probe" / "probe.pdf"
    probe.parent.mkdir()
    PaddingBackend(lambda _profile: 0).compress(
        image_pdf, probe, Profile(None, 0.15, 1.0, "probe"), timeout=1
    )
    return probe.stat().st_size
