"""Compression orchestration: inspect, search, validate, and publish.

The engine does not print, parse arguments, or import a user interface.
Progress is reported through an optional callback of typed events, and the
outcome is returned as a :class:`CompressionResult`. Expected failures other
than a missed size target raise :class:`CompressionError`; filesystem
failures may raise :class:`OSError`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from chonk.backends import CompressionBackend
from chonk.backends.ghostscript import GhostscriptBackend
from chonk.inspection import pdf_page_count
from chonk.models import (
    AttemptRecord,
    AttemptStarted,
    CandidateMeasured,
    CompressionError,
    CompressionRequest,
    CompressionResult,
    InputInspected,
    InvalidRequestError,
    OutputExistsError,
    Profile,
    ProgressCallback,
    ResultStatus,
)
from chonk.search import Candidate, build_profiles, choose_profiles
from chonk.validation.structure import validate_pdf
from chonk.validation.visual import compare_visual_similarity


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_request(request: CompressionRequest) -> None:
    """Raise :class:`InvalidRequestError` if the request is outside supported limits."""
    if not _is_int(request.target_bytes) or request.target_bytes <= 0:
        raise InvalidRequestError("The target size must be a positive whole number of bytes.")
    for name in ("min_dpi", "max_dpi", "max_attempts", "timeout_seconds", "comparison_dpi"):
        if not _is_int(getattr(request, name)):
            raise InvalidRequestError(f"{name} must be an integer.")
    if request.min_dpi < 1 or request.max_dpi < 1:
        raise InvalidRequestError("DPI values must be positive integers.")
    if request.min_dpi > request.max_dpi:
        raise InvalidRequestError("The minimum DPI cannot be greater than the maximum DPI.")
    if request.max_attempts < 2:
        raise InvalidRequestError("The maximum number of attempts must be at least 2.")
    if request.timeout_seconds < 1:
        raise InvalidRequestError("The timeout must be a positive number of seconds.")
    if request.comparison_dpi < 36 or request.comparison_dpi > 600:
        raise InvalidRequestError("The comparison DPI must be between 36 and 600.")


def _record(candidate: Candidate) -> AttemptRecord:
    return AttemptRecord(
        profile_index=candidate.profile_index,
        profile=candidate.profile,
        size_bytes=candidate.size,
        visual_similarity=candidate.visual_similarity,
    )


def _evaluate_candidate(
    backend: CompressionBackend,
    source: Path,
    workdir: Path,
    profile_index: int,
    profile: Profile,
    expected_pages: int,
    comparison_dpi: int,
    timeout: int,
) -> Candidate:
    candidate_path = workdir / f"candidate-{profile_index:03d}.pdf"
    backend.compress(source, candidate_path, profile, timeout=timeout)
    validate_pdf(candidate_path, expected_pages)
    size = candidate_path.stat().st_size
    if size <= 0:
        raise CompressionError("Ghostscript created an empty output PDF.")
    similarity = compare_visual_similarity(source, candidate_path, comparison_dpi)
    return Candidate(profile_index, profile, candidate_path, size, similarity)


def _publish(
    candidate: Path,
    output: Path,
    expected_pages: int,
    target_bytes: int,
    overwrite: bool,
) -> None:
    """Copy a checked candidate to ``output`` through a staged file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{output.stem}.", suffix=".tmp.pdf", dir=output.parent
    )
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copyfile(candidate, staged)
        validate_pdf(staged, expected_pages)
        if staged.stat().st_size > target_bytes:
            raise CompressionError(
                "The staged output is larger than the requested size ceiling."
            )
        if output.exists() and not overwrite:
            raise OutputExistsError(output, appeared_during_run=True)
        os.replace(staged, output)
    finally:
        staged.unlink(missing_ok=True)


def compress_pdf(
    request: CompressionRequest,
    *,
    backend: CompressionBackend | None = None,
    on_progress: ProgressCallback | None = None,
) -> CompressionResult:
    """Compress ``request.source`` to at most ``request.target_bytes``.

    ``backend`` defaults to an auto-discovered Ghostscript. The source file is
    never modified. When no tested profile fits, the result status is
    ``TARGET_NOT_MET`` and no output is written.
    """
    validate_request(request)

    def emit(event) -> None:
        if on_progress is not None:
            on_progress(event)

    source = Path(request.source).expanduser().resolve(strict=True)
    if not source.is_file():
        raise CompressionError(f"Input is not a file: {source}")

    output = Path(request.output).expanduser().resolve()
    if output.suffix.lower() != ".pdf":
        raise CompressionError("Output filename must end in .pdf.")
    if output == source:
        raise CompressionError("Output path must be different from the input path.")
    if output.exists() and not request.overwrite:
        raise OutputExistsError(output)

    expected_pages = pdf_page_count(source)
    if backend is None:
        backend = GhostscriptBackend.discover()
    profiles = build_profiles(request.max_dpi, request.min_dpi)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = source.stat().st_size

    emit(
        InputInspected(
            source=source,
            source_bytes=source_bytes,
            page_count=expected_pages,
            target_bytes=request.target_bytes,
            profile_count=len(profiles),
        )
    )

    attempts: list[AttemptRecord] = []
    with tempfile.TemporaryDirectory(prefix="pdf-compress-") as temporary_dir:
        workdir = Path(temporary_dir)

        def run_candidate(index: int, profile: Profile) -> Candidate:
            emit(AttemptStarted(profile_index=index, profile_count=len(profiles), profile=profile))
            candidate = _evaluate_candidate(
                backend,
                source,
                workdir,
                index,
                profile,
                expected_pages,
                request.comparison_dpi,
                request.timeout_seconds,
            )
            record = _record(candidate)
            attempts.append(record)
            emit(CandidateMeasured(attempt=record))
            return candidate

        best, smallest, _ = choose_profiles(
            profiles,
            request.target_bytes,
            request.max_attempts,
            run_candidate,
        )

        common = dict(
            source=source,
            target_bytes=request.target_bytes,
            source_bytes=source_bytes,
            page_count=expected_pages,
            comparison_dpi=request.comparison_dpi,
            smallest=_record(smallest),
            attempts=tuple(attempts),
        )
        if best is None:
            return CompressionResult(
                status=ResultStatus.TARGET_NOT_MET, output=None, selected=None, **common
            )

        if best.size > request.target_bytes:
            raise CompressionError("Internal error: selected output exceeds the size ceiling.")

        _publish(best.path, output, expected_pages, request.target_bytes, request.overwrite)

    return CompressionResult(
        status=ResultStatus.READY, output=output, selected=_record(best), **common
    )
