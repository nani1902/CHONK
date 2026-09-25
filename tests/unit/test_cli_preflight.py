"""Baseline CLI refusals that happen before (or instead of) running Ghostscript.

Ghostscript is replaced by a recorder so these tests run anywhere and can
assert whether the backend would have been invoked at all.
"""

from __future__ import annotations

import os

import pytest

import pdf_compressor
from support import FileState, copy_fixture
from gaps import known_gap


class BackendRecorder:
    """Stands in for subprocess.run; records calls and refuses to run."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, command, *args, **kwargs):
        self.calls.append(list(command))
        raise OSError("Ghostscript is disabled in preflight tests")


@pytest.fixture
def backend(monkeypatch) -> BackendRecorder:
    recorder = BackendRecorder()
    monkeypatch.setattr(pdf_compressor, "find_ghostscript", lambda explicit: "fake-gs")
    monkeypatch.setattr(pdf_compressor.subprocess, "run", recorder)
    return recorder


def assert_no_output_written(directory, source):
    assert sorted(os.listdir(directory)) == [source.name]


@pytest.mark.parametrize("name", ["encrypted-user-password", "encrypted-owner-only"])
def test_encrypted_input_is_rejected_before_ghostscript(corpus_dir, tmp_path, run_cli, backend, name):
    source = copy_fixture(corpus_dir, name, tmp_path)
    before = FileState.of(source)
    result = run_cli(source, "--target-size", "1MB")
    assert result.exit_code == 2
    assert "password-protected" in result.stderr
    assert backend.calls == []
    assert FileState.of(source) == before
    assert_no_output_written(tmp_path, source)


@pytest.mark.parametrize("name", ["malformed-not-pdf", "malformed-truncated"])
def test_unreadable_input_is_rejected_before_ghostscript(corpus_dir, tmp_path, run_cli, backend, name):
    source = copy_fixture(corpus_dir, name, tmp_path)
    before = FileState.of(source)
    result = run_cli(source, "--target-size", "1MB")
    assert result.exit_code == 2
    assert "Could not read input PDF" in result.stderr
    assert backend.calls == []
    assert FileState.of(source) == before
    assert_no_output_written(tmp_path, source)


def test_backend_failure_writes_nothing_and_keeps_the_input(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    before = FileState.of(source)
    result = run_cli(source, "--target-size", "1MB")
    assert result.exit_code == 2
    assert "Could not start Ghostscript" in result.stderr
    assert len(backend.calls) == 1
    assert FileState.of(source) == before
    assert_no_output_written(tmp_path, source)


def test_missing_ghostscript_is_reported(corpus_dir, tmp_path, run_cli, monkeypatch):
    monkeypatch.setattr(pdf_compressor.shutil, "which", lambda name: None)
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    result = run_cli(source, "--target-size", "1MB")
    assert result.exit_code == 2
    assert "Ghostscript was not found" in result.stderr
    assert_no_output_written(tmp_path, source)


def test_missing_input_is_reported(tmp_path, run_cli, backend):
    result = run_cli(tmp_path / "absent.pdf", "--target-size", "1MB")
    assert result.exit_code == 2
    assert result.stderr.startswith("Error:")
    assert backend.calls == []


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--min-dpi", "0"], "DPI values must be positive"),
        (["--min-dpi", "300", "--max-dpi", "150"], "cannot be greater than --max-dpi"),
        (["--max-attempts", "1"], "--max-attempts must be at least 2"),
        (["--timeout", "0"], "--timeout must be a positive"),
        (["--comparison-dpi", "35"], "--comparison-dpi must be between 36 and 600"),
        (["--comparison-dpi", "601"], "--comparison-dpi must be between 36 and 600"),
    ],
)
def test_invalid_options_are_rejected(corpus_dir, tmp_path, run_cli, backend, options, message):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    result = run_cli(source, "--target-size", "1MB", *options)
    assert result.exit_code == 2
    assert message in result.stderr
    assert backend.calls == []
    assert_no_output_written(tmp_path, source)


@pytest.mark.parametrize("size", ["0", "5TB", "abc"])
def test_invalid_target_size_is_a_usage_error(corpus_dir, tmp_path, run_cli, size):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        run_cli(source, "--target-size", size)
    assert exit_info.value.code == 2  # argparse usage error


def test_output_may_not_be_the_input(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    before = FileState.of(source)
    result = run_cli(source, "--target-size", "1MB", "--output", source, "--force")
    assert result.exit_code == 2
    assert "must be different from the input" in result.stderr
    assert FileState.of(source) == before


def test_output_symlink_to_the_input_is_refused(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    alias = tmp_path / "alias.pdf"
    alias.symlink_to(source)
    before = FileState.of(source)
    result = run_cli(source, "--target-size", "1MB", "--output", alias, "--force")
    assert result.exit_code == 2
    assert "must be different from the input" in result.stderr
    assert FileState.of(source) == before


def test_output_must_be_a_pdf_filename(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    result = run_cli(source, "--target-size", "1MB", "--output", tmp_path / "out.txt")
    assert result.exit_code == 2
    assert "must end in .pdf" in result.stderr
    assert_no_output_written(tmp_path, source)


def test_existing_output_is_not_replaced_without_force(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "text-statement", tmp_path)
    existing = tmp_path / "text-statement-compressed.pdf"
    existing.write_bytes(b"SYNTHETIC existing output")
    result = run_cli(source, "--target-size", "1MB")
    assert result.exit_code == 2
    assert "Output already exists" in result.stderr
    assert existing.read_bytes() == b"SYNTHETIC existing output"
    assert backend.calls == []


@known_gap("CHONK-005", "signed input reaches Ghostscript, which invalidates the signature")
def test_signed_input_is_blocked_before_ghostscript(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "signed-pkcs7", tmp_path)
    run_cli(source, "--target-size", "1MB")
    assert backend.calls == []


@known_gap("CHONK-005", "form input reaches Ghostscript, which drops the AcroForm")
def test_form_input_is_blocked_before_ghostscript(corpus_dir, tmp_path, run_cli, backend):
    source = copy_fixture(corpus_dir, "form-acroform", tmp_path)
    run_cli(source, "--target-size", "1MB")
    assert backend.calls == []
