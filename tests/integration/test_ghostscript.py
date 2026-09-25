"""End-to-end checks with the real Ghostscript backend and legacy launcher."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chonk import CompressionRequest, ResultStatus, compress_pdf
from chonk.backends.ghostscript import GhostscriptBackend
from conftest import make_image_pdf, sha256

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not any(shutil.which(name) for name in ("gs", "gswin64c.exe", "gswin32c.exe")),
    reason="Ghostscript is not installed",
)


@pytest.fixture(scope="module")
def scan_pdf(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("fixtures") / "scan.pdf"
    return make_image_pdf(path, pages=2, size=(600, 800))


def test_engine_meets_ceiling_with_ghostscript(scan_pdf, tmp_path):
    source_hash = sha256(scan_pdf)
    target = scan_pdf.stat().st_size // 3
    output = tmp_path / "out.pdf"
    result = compress_pdf(
        CompressionRequest(source=scan_pdf, output=output, target_bytes=target),
        backend=GhostscriptBackend.discover(),
    )
    assert result.status is ResultStatus.READY
    assert output.stat().st_size == result.selected.size_bytes <= target
    assert sha256(scan_pdf) == source_hash


def run_legacy(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "pdf_compressor.py"), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=600,
    )


def test_legacy_cli_success(scan_pdf, tmp_path):
    source_hash = sha256(scan_pdf)
    output = tmp_path / "legacy.pdf"
    target = scan_pdf.stat().st_size // 3
    completed = run_legacy(str(scan_pdf), "--target-size", f"{target}B", "--output", str(output))

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith(f"Saved {output.resolve()}\n")
    assert "Render similarity:" in completed.stdout
    assert "Trying profile" not in completed.stdout
    assert completed.stderr.startswith(f"Input: {scan_pdf.resolve()} (")
    assert "Trying profile 1/70: keep source resolution; highest image quality" in completed.stderr
    assert output.stat().st_size <= target
    assert sha256(scan_pdf) == source_hash


def test_legacy_cli_default_output_name(scan_pdf, tmp_path):
    source = tmp_path / "copy.pdf"
    shutil.copyfile(scan_pdf, source)
    completed = run_legacy(str(source), "--target-size", "100MB")
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "copy-compressed.pdf").is_file()


def test_legacy_cli_target_not_met(scan_pdf, tmp_path):
    output = tmp_path / "never.pdf"
    completed = run_legacy(str(scan_pdf), "--target-size", "1KB", "--output", str(output))
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.rstrip().splitlines()[-1].startswith(
        "Error: No tested profile reached the size ceiling."
    )
    assert "try a lower --min-dpi or a larger --target-size. No output was written." in completed.stderr
    assert not output.exists()


def test_legacy_cli_refuses_existing_output(scan_pdf, tmp_path):
    output = tmp_path / "exists.pdf"
    output.write_bytes(b"keep")
    completed = run_legacy(str(scan_pdf), "--target-size", "100MB", "--output", str(output))
    assert completed.returncode == 2
    assert completed.stderr == (
        f"Error: Output already exists: {output.resolve()} (pass --force to replace it).\n"
    )
    assert output.read_bytes() == b"keep"
