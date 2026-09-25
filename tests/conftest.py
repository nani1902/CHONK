"""Shared synthetic fixtures. No real documents are used or committed."""

from __future__ import annotations

import hashlib
import os
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
from PIL import Image
from pypdf import PdfWriter

import pdf_compressor
from chonk.models import Profile
from corpus import FIXTURES


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


SOURCE_BULK_KEY = "/ChonkTestSourceBulk"
SOURCE_BULK_BYTES = 200_000


def add_source_bulk(path: Path, size: int = SOURCE_BULK_BYTES) -> Path:
    """Pad ``path`` with metadata that ``PaddingBackend`` drops, so candidates
    are smaller than the source, as with real compression. Without it every
    candidate would be larger and a source under the ceiling would take the
    unchanged-source path instead of the search."""
    writer = PdfWriter(clone_from=str(path))
    writer.add_metadata({SOURCE_BULK_KEY: "x" * size})
    with path.open("wb") as stream:
        writer.write(stream)
    return path


class PaddingBackend:
    """Test backend: copies the source's pages, without its document info
    (and so without ``add_source_bulk`` padding), and pads the metadata so
    output size is a controlled function of the profile. Rendering is
    unchanged."""

    def __init__(self, padding_for: Callable[[Profile], int]):
        self.padding_for = padding_for
        self.calls: list[Profile] = []

    def compress(self, source: Path, output: Path, profile: Profile, *, timeout: int) -> None:
        self.calls.append(profile)
        writer = PdfWriter()
        writer.append(str(source))  # Pages only: the document info is not copied.
        writer.add_metadata({"/ChonkTestPadding": "x" * self.padding_for(profile)})
        with output.open("wb") as stream:
            writer.write(stream)


@pytest.fixture
def image_pdf(tmp_path: Path) -> Path:
    return add_source_bulk(make_image_pdf(tmp_path / "source.pdf", pages=2))


@pytest.fixture
def unpadded_size(image_pdf: Path, tmp_path: Path) -> int:
    """Size of a PaddingBackend output with zero padding."""
    probe = tmp_path / "probe" / "probe.pdf"
    probe.parent.mkdir()
    PaddingBackend(lambda _profile: 0).compress(
        image_pdf, probe, Profile(None, 0.15, 1.0, "probe"), timeout=1
    )
    return probe.stat().st_size


@pytest.fixture(scope="session")
def corpus_dir(tmp_path_factory) -> Path:
    """The whole synthetic corpus, written once per test session."""
    directory = tmp_path_factory.mktemp("corpus")
    for spec in FIXTURES:
        (directory / spec.filename).write_bytes(spec.build())
    return directory


@dataclass(frozen=True)
class CliResult:
    exit_code: int
    stdout: str
    stderr: str


@pytest.fixture
def run_cli(capsys):
    """Run the legacy CLI in-process and capture its streams."""

    def invoke(*args: str | os.PathLike) -> CliResult:
        capsys.readouterr()
        code = pdf_compressor.main([str(arg) for arg in args])
        captured = capsys.readouterr()
        return CliResult(code, captured.out, captured.err)

    return invoke


def _ghostscript_version(executable: str) -> str:
    return subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(scope="session")
def ghostscript() -> str:
    try:
        executable = pdf_compressor.find_ghostscript(None)
    except pdf_compressor.CompressionError:
        pytest.skip("Ghostscript is not installed")
    return executable


def pytest_report_header(config):
    try:
        executable = pdf_compressor.find_ghostscript(None)
    except pdf_compressor.CompressionError:
        return "ghostscript: not found (integration tests will be skipped)"
    return f"ghostscript: {executable} {_ghostscript_version(executable)}"


def pytest_collection_modifyitems(items):
    for item in items:
        if "ghostscript" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.ghostscript)
