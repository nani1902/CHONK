"""Typed requests, results, progress events, and errors shared by all callers.

These are the minimal types needed to separate the engine from its CLI and
desktop callers. Versioned schemas, policies, check states, and reason codes
are defined separately once the result contract is formalized.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class CompressionError(Exception):
    """A user-facing compression error."""


class InvalidRequestError(CompressionError):
    """The request is malformed or outside supported limits."""


class OutputExistsError(CompressionError):
    """The output path exists and the request does not allow replacing it."""

    def __init__(self, path: Path, *, appeared_during_run: bool = False):
        self.path = path
        self.appeared_during_run = appeared_during_run
        if appeared_during_run:
            message = f"Output appeared while compressing: {path}."
        else:
            message = f"Output already exists: {path}."
        super().__init__(message)


@dataclass(frozen=True)
class Profile:
    dpi: int | None
    qfactor: float
    clarity_rank: float
    label: str


@dataclass(frozen=True)
class CompressionRequest:
    """Everything the engine needs to compress one PDF.

    ``output`` is always explicit; default naming is a caller convention.
    ``target_bytes`` is an exact byte ceiling; parse human sizes with
    :func:`chonk.sizes.parse_size` first.
    """

    source: Path
    output: Path
    target_bytes: int
    overwrite: bool = False
    min_dpi: int = 72
    max_dpi: int = 600
    max_attempts: int = 16
    timeout_seconds: int = 900
    comparison_dpi: int = 150


@dataclass(frozen=True)
class AttemptRecord:
    """Measurements for one tested profile. Temporary paths are not retained."""

    profile_index: int
    profile: Profile
    size_bytes: int
    visual_similarity: float


class ResultStatus(str, Enum):
    READY = "ready"
    TARGET_NOT_MET = "target_not_met"


@dataclass(frozen=True)
class CompressionResult:
    status: ResultStatus
    source: Path
    output: Path | None
    """Published output path; ``None`` unless the status is ``READY``."""
    target_bytes: int
    source_bytes: int
    page_count: int
    comparison_dpi: int
    selected: AttemptRecord | None
    """The published attempt; ``None`` unless the status is ``READY``."""
    smallest: AttemptRecord
    attempts: tuple[AttemptRecord, ...]
    """Every tested profile, in the order it was tried."""

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)


@dataclass(frozen=True)
class InputInspected:
    """The source was read and the search is about to start."""

    source: Path
    source_bytes: int
    page_count: int
    target_bytes: int
    profile_count: int


@dataclass(frozen=True)
class AttemptStarted:
    profile_index: int
    profile_count: int
    profile: Profile


@dataclass(frozen=True)
class CandidateMeasured:
    attempt: AttemptRecord


ProgressEvent = InputInspected | AttemptStarted | CandidateMeasured
ProgressCallback = Callable[[ProgressEvent], None]
