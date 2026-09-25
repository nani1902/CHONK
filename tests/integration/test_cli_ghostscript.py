"""Baseline end-to-end behavior with the real Ghostscript backend.

Skipped when Ghostscript is not installed. Searches use a small attempt
budget and a low comparison resolution to keep the suite fast; the byte
ceiling and file-safety guarantees do not depend on those settings.
"""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfReader

import pdf_compressor
from corpus import FIXTURES, fixture_bytes
from evaluation import evaluate
from gaps import known_gap
from support import FileState, copy_fixture

FAST = ("--max-attempts", "4", "--comparison-dpi", "36")


def page_count(path: Path) -> int:
    return len(PdfReader(io.BytesIO(path.read_bytes()), strict=False).pages)


def compress(run_cli, corpus_dir, directory, name, target, *extra):
    source = copy_fixture(corpus_dir, name, directory)
    before = FileState.of(source)
    output = directory / f"{name}-out.pdf"
    result = run_cli(source, "--target-size", target, "--output", output, *FAST, *extra)
    assert FileState.of(source) == before, "the input file changed"
    return source, output, result


def assert_only(directory: Path, *names: str) -> None:
    assert sorted(os.listdir(directory)) == sorted(names), "unexpected or leftover files"


# --- Invariants across the whole corpus -----------------------------------------


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda spec: spec.name)
def test_generous_ceiling(ghostscript, corpus_dir, tmp_path, run_cli, spec):
    target = 10 * len(fixture_bytes(spec.name))
    source, output, result = compress(run_cli, corpus_dir, tmp_path, spec.name, f"{target}B")

    assert (result.exit_code == 0) is spec.baseline_accepts, result.stderr
    if result.exit_code == 0:
        assert output.stat().st_size <= target
        assert page_count(output) == page_count(source)
        assert_only(tmp_path, source.name, output.name)
    else:
        assert result.exit_code == 2
        assert_only(tmp_path, source.name)


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda spec: spec.name)
def test_tight_ceiling(ghostscript, corpus_dir, tmp_path, run_cli, spec):
    target = max(1, len(fixture_bytes(spec.name)) * 2 // 5)
    source, output, result = compress(run_cli, corpus_dir, tmp_path, spec.name, f"{target}B")

    if result.exit_code == 0:
        assert output.stat().st_size <= target
        assert_only(tmp_path, source.name, output.name)
    else:
        assert result.exit_code == 2
        assert_only(tmp_path, source.name)


# --- Units and ceilings ------------------------------------------------------------


@pytest.mark.parametrize(("target", "limit"), [("300KB", 300_000), ("300KiB", 307_200)])
def test_ceiling_units_are_respected_end_to_end(ghostscript, corpus_dir, tmp_path, run_cli, target, limit):
    _, output, result = compress(run_cli, corpus_dir, tmp_path, "image-mixed", target)
    assert result.exit_code == 0, result.stderr
    assert output.stat().st_size <= limit
    assert f"({limit:,} bytes)" in result.stderr  # the echoed target is exact


def test_success_summary(ghostscript, corpus_dir, tmp_path, run_cli):
    _, output, result = compress(run_cli, corpus_dir, tmp_path, "image-mixed", "300KB")
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == f"Saved {output}"
    assert lines[1].startswith("Size: ") and "ceiling 292.97 KiB (300,000 bytes)" in lines[1]
    assert lines[2].startswith("Profile: ") and "attempt(s)" in lines[2]
    assert lines[3].startswith("Render similarity: ") and lines[3].endswith("at 36 dpi")


def test_target_not_met_writes_nothing(ghostscript, corpus_dir, tmp_path, run_cli):
    source, _, result = compress(run_cli, corpus_dir, tmp_path, "image-scan", "20KB")
    assert result.exit_code == 2
    assert "No tested profile reached the size ceiling" in result.stderr
    assert "No output was written" in result.stderr
    assert_only(tmp_path, source.name)


def test_default_output_name(ghostscript, corpus_dir, tmp_path, run_cli):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    result = run_cli(source, "--target-size", "1MB", *FAST)
    assert result.exit_code == 0, result.stderr
    assert_only(tmp_path, source.name, "text-statement-compressed.pdf")


# --- Output safety -------------------------------------------------------------------


def test_force_replaces_an_existing_output(ghostscript, corpus_dir, tmp_path, run_cli):
    existing = tmp_path / "text-statement-out.pdf"
    existing.write_bytes(b"SYNTHETIC stale output")
    _, output, result = compress(
        run_cli, corpus_dir, tmp_path, "text-statement", "1MB", "--force"
    )
    assert result.exit_code == 0, result.stderr
    assert output == existing and output.read_bytes().startswith(b"%PDF")


def test_forced_output_over_a_hard_link_to_the_input_keeps_the_input(
    ghostscript, corpus_dir, tmp_path, run_cli
):
    """os.replace swaps the directory entry, so the shared inode is untouched."""
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    alias = tmp_path / "alias.pdf"
    os.link(source, alias)
    before = FileState.of(source)
    result = run_cli(source, "--target-size", "1MB", "--output", alias, "--force", *FAST)
    assert result.exit_code == 0, result.stderr
    assert FileState.of(source).sha256 == before.sha256
    assert source.stat().st_ino != alias.stat().st_ino


# --- Output quality, judged by the independent evaluation checks -------------------


PRESERVED = ["text-statement", "text-multipage", "text-tiny", "image-scan", "image-mixed",
             "annotations", "already-small", "malformed-bad-xref"]


@pytest.mark.parametrize("name", PRESERVED)
def test_output_passes_evaluation(ghostscript, corpus_dir, tmp_path, run_cli, name):
    target = 10 * len(fixture_bytes(name))
    source, output, result = compress(run_cli, corpus_dir, tmp_path, name, f"{target}B")
    assert result.exit_code == 0, result.stderr
    evaluation = evaluate(source.read_bytes(), output.read_bytes(), target_bytes=target)
    assert evaluation.passed, evaluation.findings


def test_aggressive_output_passes_evaluation(ghostscript, corpus_dir, tmp_path, run_cli):
    source, output, result = compress(run_cli, corpus_dir, tmp_path, "image-scan", "120KB")
    assert result.exit_code == 0, result.stderr
    evaluation = evaluate(source.read_bytes(), output.read_bytes(), target_bytes=120_000)
    assert evaluation.passed, evaluation.findings


@known_gap("CHONK-007", "Ghostscript drops the AcroForm and flattens field values, silently")
def test_form_fields_survive_or_the_input_is_blocked(ghostscript, corpus_dir, tmp_path, run_cli):
    source, output, result = compress(run_cli, corpus_dir, tmp_path, "form-acroform", "1MB")
    if result.exit_code == 0:
        evaluation = evaluate(source.read_bytes(), output.read_bytes())
        assert "form_fields" not in evaluation.failed_checks, evaluation.findings


@known_gap("CHONK-005", "a signed input is rewritten and its signature silently invalidated")
def test_signed_input_is_not_rewritten(ghostscript, corpus_dir, tmp_path, run_cli):
    _, output, result = compress(run_cli, corpus_dir, tmp_path, "signed-pkcs7", "1MB")
    assert result.exit_code != 0 and not output.exists()


@known_gap("CHONK-007", "an input that already fits is still rewritten")
def test_already_small_input_is_published_unchanged(ghostscript, corpus_dir, tmp_path, run_cli):
    source, output, result = compress(run_cli, corpus_dir, tmp_path, "already-small", "1MB")
    assert result.exit_code == 0
    assert output.read_bytes() == source.read_bytes()


@known_gap("CHONK-007", "the rewrite can exceed a ceiling the original already meets")
def test_input_that_already_fits_is_never_reported_as_target_not_met(
    ghostscript, corpus_dir, tmp_path, run_cli
):
    size = len(fixture_bytes("already-small"))
    _, _, result = compress(run_cli, corpus_dir, tmp_path, "already-small", f"{size}B")
    assert result.exit_code == 0, result.stderr


# --- The real profile ladder is not monotonic in size -------------------------------


def test_real_ladder_sizes_are_not_monotonic(ghostscript, corpus_dir, tmp_path):
    """Evidence for CHONK-010: stepping down the clarity ladder can grow the file,
    and QFactor has no effect at source resolution on JPEG-only input because
    -dPassThroughJPEGImages keeps the original image data."""
    source = copy_fixture(corpus_dir, "image-scan", tmp_path)
    profiles = pdf_compressor.build_profiles(600, 72)
    # Ghostscript stamps the run time into dates and the XMP document UUID.
    # On some builds (seen with 10.08.0 on Windows) that shifts the output by a
    # byte from one second to the next, so pin the clock to compare exact sizes.
    env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000"}
    sizes = []
    for index, profile in enumerate(profiles[:6]):
        output = tmp_path / f"candidate-{index}.pdf"
        command = pdf_compressor.make_ghostscript_command(ghostscript, source, output, profile)
        subprocess.run(command, check=True, capture_output=True, timeout=120, env=env)
        sizes.append(output.stat().st_size)

    increases = [i for i in range(1, len(sizes)) if sizes[i] > sizes[i - 1]]
    assert increases, f"sizes along the ladder: {sizes}"
    source_resolution = {
        size for size, profile in zip(sizes, profiles) if profile.dpi is None
    }
    assert len(source_resolution) == 1, sizes
