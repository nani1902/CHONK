"""Compression orchestration: snapshot, inspect, search, validate, and publish.

The engine does not print, parse arguments, or import a user interface.
Progress is reported through an optional callback of typed events, and the
outcome is returned as a :class:`CompressionResult`.

Every step works on one private snapshot of the source, copied from a single
read into a job directory, so inspection, the backend, and publication all see
the same bytes. The original is never written. Before anything is published
the original is compared with the snapshot again; if it changed, the result is
``BLOCKED`` with ``SOURCE_CHANGED`` and nothing is written.

Preflight inspection runs before any backend: input the request's policy does
not support, cannot be inspected conclusively, or cannot be read is returned as
``BLOCKED`` without running a backend. A supported source that already fits the
byte ceiling is published byte for byte, with ``completion_basis``
``unchanged_source``, and no backend runs either. Expected failures other than a
missed size target or a blocked input raise :class:`CompressionError`;
filesystem failures may raise :class:`OSError`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chonk.backends import CompressionBackend
from chonk.backends.ghostscript import GhostscriptBackend
from chonk.inspection import (
    NO_TEXT_LAYER_PAGE_KINDS,
    InspectionReport,
    PageKind,
    PreflightDecision,
    evaluate_preflight,
    inspect_pdf,
)
from chonk.models import (
    MAX_ATTEMPTS,
    MAX_COMPARISON_DPI,
    MAX_DEADLINE_SECONDS,
    MAX_DPI,
    MAX_TARGET_BYTES,
    MIN_ATTEMPTS,
    MIN_COMPARISON_DPI,
    AttemptRecord,
    AttemptStarted,
    CandidateMeasured,
    CheckId,
    CheckState,
    CompletionBasis,
    CompressionError,
    CompressionRequest,
    CompressionResult,
    ContractError,
    InputInspected,
    InvalidRequestError,
    OutputExistsError,
    Profile,
    ProgressCallback,
    ReasonCode,
    ResultStatus,
)
from chonk.policies import PolicyDefinition, get_policy
from chonk.search import Candidate, build_profiles, choose_profiles
from chonk.validation.structure import validate_pdf
from chonk.validation.visual import compare_visual_similarity


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_request(request: CompressionRequest) -> PolicyDefinition:
    """Raise :class:`InvalidRequestError` if the request is outside supported limits.

    Returns the definition of the requested policy.
    """
    if (
        not _is_int(request.target_bytes)
        or not 1 <= request.target_bytes <= MAX_TARGET_BYTES
    ):
        raise InvalidRequestError(
            "The target size must be a whole number of bytes from 1 to "
            f"{MAX_TARGET_BYTES:,}."
        )
    for name in ("min_dpi", "max_dpi", "max_attempts", "timeout_seconds", "comparison_dpi"):
        if not _is_int(getattr(request, name)):
            raise InvalidRequestError(f"{name} must be an integer.")
    if not (1 <= request.min_dpi <= MAX_DPI and 1 <= request.max_dpi <= MAX_DPI):
        raise InvalidRequestError(f"DPI values must be integers from 1 to {MAX_DPI}.")
    if request.min_dpi > request.max_dpi:
        raise InvalidRequestError("The minimum DPI cannot be greater than the maximum DPI.")
    if not MIN_ATTEMPTS <= request.max_attempts <= MAX_ATTEMPTS:
        raise InvalidRequestError(
            f"The maximum number of attempts must be from {MIN_ATTEMPTS} to {MAX_ATTEMPTS}."
        )
    if not 1 <= request.timeout_seconds <= MAX_DEADLINE_SECONDS:
        raise InvalidRequestError(
            f"The timeout must be from 1 to {MAX_DEADLINE_SECONDS:,} seconds."
        )
    if not MIN_COMPARISON_DPI <= request.comparison_dpi <= MAX_COMPARISON_DPI:
        raise InvalidRequestError(
            f"The comparison DPI must be between {MIN_COMPARISON_DPI} and {MAX_COMPARISON_DPI}."
        )
    try:
        return get_policy(request.policy_id)
    except ContractError as exc:
        raise InvalidRequestError(exc.message) from exc


_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class _Snapshot:
    """A private copy of the source taken from one read."""

    path: Path
    size: int
    sha256: str


class _SourceChanged(Exception):
    """The source changed while it was being copied."""


def _snapshot_source(source: Path, workdir: Path) -> _Snapshot:
    """Copy ``source`` into ``workdir``, hashing the bytes as they are read.

    Raises :class:`_SourceChanged` if the file's size or modification time
    moved during the copy, or the copy is not the size the file reported.
    """
    destination = workdir / "source.pdf"
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        before = os.fstat(reader.fileno())
        while chunk := reader.read(_CHUNK):
            digest.update(chunk)
            writer.write(chunk)
            size += len(chunk)
        after = os.fstat(reader.fileno())
    if size != before.st_size or (after.st_size, after.st_mtime_ns) != (
        before.st_size,
        before.st_mtime_ns,
    ):
        raise _SourceChanged
    return _Snapshot(destination, size, digest.hexdigest())


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _source_unchanged(source: Path, snapshot: _Snapshot) -> bool:
    """The file at ``source`` still holds exactly the snapshot bytes."""
    try:
        return _sha256(source) == (snapshot.sha256, snapshot.size)
    except OSError:
        return False


_NO_TEXT_PAGE_KINDS = NO_TEXT_LAYER_PAGE_KINDS | {PageKind.BLANK}


def _unchanged_checks(
    policy: PolicyDefinition, report: InspectionReport, preflight: PreflightDecision
) -> dict[CheckId, CheckState]:
    """Checks for publishing the source's own bytes under ``policy``.

    Identical bytes have the same pages, geometry, extracted text, and
    rendering as the source, so those checks pass by identity rather than by
    comparison. Text preservation is ``not_applicable`` only when no page has a
    text layer (every page is image-only, graphics-only, or blank); the policy
    decides whether that is acceptable. Feature support and the text layer keep
    the states preflight decided.
    """
    no_text = all(page.kind in _NO_TEXT_PAGE_KINDS for page in report.pages)
    evidence = {
        CheckId.SIZE_CEILING: CheckState.PASS,
        CheckId.PAGE_STRUCTURE: CheckState.PASS,
        CheckId.EXTRACTED_TEXT: CheckState.NOT_APPLICABLE if no_text else CheckState.PASS,
        CheckId.TEXT_LAYER: preflight.text_layer.state,
        CheckId.VISUAL_POLICY: CheckState.PASS,
        **preflight.checks,
    }
    return {check: evidence[check] for check in policy.checks}


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
    artifact: Path,
    output: Path,
    expected_pages: int,
    target_bytes: int,
    overwrite: bool,
    expected_sha256: str,
) -> None:
    """Copy checked bytes to ``output`` through a staged file.

    The staged copy must hash to ``expected_sha256``, so the published file is
    exactly the artifact that was checked.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{output.stem}.", suffix=".tmp.pdf", dir=output.parent
    )
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copyfile(artifact, staged)
        if _sha256(staged)[0] != expected_sha256:
            raise CompressionError("The staged output does not match the checked bytes.")
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
    never modified. The source is snapshotted and inspected first:

    * If the request's policy does not allow it, the result status is
      ``BLOCKED``, the backend is never discovered or run, and no output is
      written, whatever the source's size.
    * If it is allowed and already at or below the ceiling, its exact bytes are
      published with ``completion_basis`` ``UNCHANGED_SOURCE``; no backend is
      discovered or run.
    * Otherwise the profile search runs. When no tested profile fits, the
      result status is ``TARGET_NOT_MET`` and no output is written.

    If the source changes before publication, the result is ``BLOCKED`` with
    ``SOURCE_CHANGED`` and no output is written. The requested policy is never
    replaced or relaxed.
    """
    policy = validate_request(request)

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

    with tempfile.TemporaryDirectory(prefix="chonk-job-") as temporary_dir:
        workdir = Path(temporary_dir)
        base = dict(
            source=source,
            target_bytes=request.target_bytes,
            comparison_dpi=request.comparison_dpi,
            policy_id=policy.policy_id,
        )

        def blocked(reasons, source_bytes, report=None, preflight=None, **extra):
            fields = dict(
                output=None,
                selected=None,
                smallest=None,
                attempts=(),
                page_count=report.page_count if report is not None else None,
                checks=preflight.checks if preflight is not None else {},
                **base,
            )
            fields.update(extra)
            return CompressionResult(
                status=ResultStatus.BLOCKED,
                source_bytes=source_bytes,
                reason_codes=tuple(reasons),
                inspection=report,
                preflight=preflight,
                **fields,
            )

        try:
            snapshot = _snapshot_source(source, workdir)
        except _SourceChanged:
            return blocked((ReasonCode.SOURCE_CHANGED,), source.stat().st_size)

        report = inspect_pdf(snapshot.path)
        preflight = evaluate_preflight(report, policy)
        if not preflight.allowed:
            return blocked(preflight.reason_codes, snapshot.size, report, preflight)
        expected_pages = report.page_count
        assert expected_pages is not None  # An allowed report was fully read.

        def source_changed(**extra):
            return blocked(
                (ReasonCode.SOURCE_CHANGED,), snapshot.size, report, preflight, **extra
            )

        if snapshot.size <= request.target_bytes:
            checks = _unchanged_checks(policy, report, preflight)
            # Unreachable for the v1 policies: every check above is closed.
            # Should a policy ever refuse the unchanged bytes, search instead.
            if policy.decide(checks).status is ResultStatus.READY:
                if not _source_unchanged(source, snapshot):
                    return source_changed()
                _publish(
                    snapshot.path,
                    output,
                    expected_pages,
                    request.target_bytes,
                    request.overwrite,
                    snapshot.sha256,
                )
                return CompressionResult(
                    status=ResultStatus.READY,
                    output=output,
                    source_bytes=snapshot.size,
                    page_count=expected_pages,
                    selected=None,
                    smallest=None,
                    attempts=(),
                    inspection=report,
                    preflight=preflight,
                    checks=checks,
                    completion_basis=CompletionBasis.UNCHANGED_SOURCE,
                    **base,
                )

        if backend is None:
            backend = GhostscriptBackend.discover()
        profiles = build_profiles(request.max_dpi, request.min_dpi)
        output.parent.mkdir(parents=True, exist_ok=True)

        emit(
            InputInspected(
                source=source,
                source_bytes=snapshot.size,
                page_count=expected_pages,
                target_bytes=request.target_bytes,
                profile_count=len(profiles),
            )
        )

        attempts: list[AttemptRecord] = []

        def run_candidate(index: int, profile: Profile) -> Candidate:
            emit(AttemptStarted(profile_index=index, profile_count=len(profiles), profile=profile))
            candidate = _evaluate_candidate(
                backend,
                snapshot.path,
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
            source_bytes=snapshot.size,
            page_count=expected_pages,
            smallest=_record(smallest),
            attempts=tuple(attempts),
            inspection=report,
            preflight=preflight,
            **base,
        )
        if best is None:
            return CompressionResult(
                status=ResultStatus.TARGET_NOT_MET,
                output=None,
                selected=None,
                checks={CheckId.SIZE_CEILING: CheckState.FAIL, **preflight.checks},
                **common,
            )

        if best.size > request.target_bytes:
            raise CompressionError("Internal error: selected output exceeds the size ceiling.")

        if not _source_unchanged(source, snapshot):
            return source_changed(smallest=_record(smallest), attempts=tuple(attempts))
        _publish(
            best.path,
            output,
            expected_pages,
            request.target_bytes,
            request.overwrite,
            _sha256(best.path)[0],
        )

    return CompressionResult(
        status=ResultStatus.READY,
        output=output,
        selected=_record(best),
        checks={CheckId.SIZE_CEILING: CheckState.PASS, **preflight.checks},
        **common,
    )
