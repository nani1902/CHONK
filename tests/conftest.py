from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import pdf_compressor
from corpus import FIXTURES


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
    """Run the baseline CLI in-process and capture its streams."""

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
