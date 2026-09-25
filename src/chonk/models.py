"""Typed requests, results, progress events, and errors shared by all callers.

Two layers live here:

* Engine types (:class:`CompressionRequest`, :class:`CompressionResult`, and
  progress events) used in-process by the CLI and desktop callers.
* The versioned result contract (schema ``1.0``): :class:`PrepareRequest`,
  :class:`PreparationResult`, result and check states, reason codes,
  completion basis, and next actions. These are the types meant to cross a
  machine boundary (JSON CLI, MCP). Construct and parse them through
  :mod:`chonk.contract`, which enforces the semantic rules; policy definitions
  are in :mod:`chonk.policies`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

if TYPE_CHECKING:
    from chonk.inspection import InspectionReport, PreflightDecision

# --- Numeric request limits, shared by engine and contract validation --------

MAX_TARGET_BYTES = 10 * 1024**3
"""10 GiB. Far above any known upload limit, far below the 2**53 - 1 range that
JSON clients can represent exactly."""
MIN_ATTEMPTS = 2
MAX_ATTEMPTS = 64
MAX_DEADLINE_SECONDS = 24 * 60 * 60
MAX_DPI = 2400
MIN_COMPARISON_DPI = 36
MAX_COMPARISON_DPI = 600


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
    :func:`chonk.sizes.parse_size` first. ``policy_id`` names the versioned
    preservation policy whose feature restrictions preflight enforces; see
    :mod:`chonk.policies`.
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
    policy_id: str = "preserve-existing-text-v1"
    """Default is :data:`chonk.policies.DEFAULT_POLICY_ID`; the literal avoids an
    import cycle and a test keeps the two equal."""


@dataclass(frozen=True)
class AttemptRecord:
    """Measurements for one tested profile. Temporary paths are not retained."""

    profile_index: int
    profile: Profile
    size_bytes: int
    visual_similarity: float


class ResultStatus(str, Enum):
    """Outcome of one file. See ``docs/product/ARCHITECTURE.md`` section 4.

    The engine currently produces ``READY``, ``TARGET_NOT_MET``, and ``BLOCKED``
    (preflight refused the input, or the source changed during the run); the
    other states are defined by the contract for the stages that produce them.
    """

    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    TARGET_NOT_MET = "target_not_met"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class CompressionResult:
    status: ResultStatus
    source: Path
    output: Path | None
    """Published output path; ``None`` unless the status is ``READY``."""
    target_bytes: int
    source_bytes: int
    """Size of the source snapshot the checks ran on; the file's reported size
    if the source changed before a snapshot could be taken."""
    page_count: int | None
    """``None`` only when preflight could not read the document."""
    comparison_dpi: int
    selected: AttemptRecord | None
    """The published attempt; ``None`` unless a compressed output is ``READY``."""
    smallest: AttemptRecord | None
    """The smallest tested attempt; ``None`` when nothing was attempted."""
    attempts: tuple[AttemptRecord, ...]
    """Every tested profile, in the order it was tried."""
    policy_id: str = "preserve-existing-text-v1"
    reason_codes: tuple[ReasonCode, ...] = ()
    """Why a ``BLOCKED`` result was refused; empty otherwise for now."""
    inspection: InspectionReport | None = None
    """The preflight report. It holds no document content."""
    preflight: PreflightDecision | None = None
    """The policy's verdict on ``inspection``."""
    checks: Mapping[CheckId, CheckState] = field(default_factory=dict)
    """Contract checks the engine has decided so far. A check that is absent was
    not evaluated and counts as ``unknown``. Only an unchanged-source result
    carries every check of its policy; for a compressed output the page,
    text, and visual checks belong to the validators (CHONK-008, CHONK-009)."""
    completion_basis: CompletionBasis | None = None
    """Why a ``READY`` result is ready, once every check its policy names is
    decided. Currently set only for ``UNCHANGED_SOURCE``."""

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def unchanged_source(self) -> bool:
        """The published output is a byte-identical copy of the source."""
        return self.completion_basis is CompletionBasis.UNCHANGED_SOURCE

    @property
    def output_bytes(self) -> int | None:
        """Size of the published output; ``None`` when nothing was published."""
        if self.status is not ResultStatus.READY:
            return None
        if self.unchanged_source:
            return self.source_bytes
        return self.selected.size_bytes if self.selected is not None else None


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


# --- Versioned contract (schema 1.0) -----------------------------------------

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})


class CheckState(str, Enum):
    """State of one named check. A required ``UNKNOWN`` is never a pass."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class CheckId(str, Enum):
    """Named checks. A policy decides which are required and which advisory."""

    SIZE_CEILING = "size_ceiling"
    """Output bytes are at or below the exact target."""
    FEATURE_SUPPORT = "feature_support"
    """Preflight found no feature the policy blocks, and nothing inconclusive."""
    PAGE_STRUCTURE = "page_structure"
    """Page count, order, geometry, and rotation match the source."""
    EXTRACTED_TEXT = "extracted_text"
    """Per-page extracted text of the output matches the source."""
    TEXT_LAYER = "text_layer"
    """Every relevant content page has a usable text layer."""
    VISUAL_POLICY = "visual_policy"
    """Page-level visual comparison met the policy's calibrated thresholds."""


class CompletionBasis(str, Enum):
    """Why a ``ready`` result is ready."""

    AUTOMATED_CHECKS = "automated_checks"
    HUMAN_REVIEW = "human_review"
    UNCHANGED_SOURCE = "unchanged_source"


class ReasonCode(str, Enum):
    """Machine-readable causes for a non-ready result. Clients branch on these,
    never on human messages."""

    # Input or scope problems (blocked)
    ENCRYPTED_INPUT = "ENCRYPTED_INPUT"
    SIGNED_INPUT = "SIGNED_INPUT"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    INSPECTION_INCONCLUSIVE = "INSPECTION_INCONCLUSIVE"
    MALFORMED_INPUT = "MALFORMED_INPUT"
    TEXT_LAYER_MISSING = "TEXT_LAYER_MISSING"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    ACCESS_DENIED = "ACCESS_DENIED"
    OUTPUT_CONFLICT = "OUTPUT_CONFLICT"
    PRESERVATION_CONSTRAINT_FAILED = "PRESERVATION_CONSTRAINT_FAILED"
    VALIDATION_INCONCLUSIVE = "VALIDATION_INCONCLUSIVE"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    # Size
    TARGET_NOT_MET = "TARGET_NOT_MET"
    # Human-resolvable uncertainty (needs_review)
    QUALITY_REVIEW_REQUIRED = "QUALITY_REVIEW_REQUIRED"
    # Runtime (failed)
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_FAILED = "BACKEND_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Cancellation
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    REVIEW_EXPIRED = "REVIEW_EXPIRED"


class NextAction(str, Enum):
    """What the caller should do next; derived from status and reasons."""

    USE_LOCAL_ARTIFACT = "use_local_artifact"
    OPEN_LOCAL_REVIEW = "open_local_review"
    INCREASE_TARGET = "increase_target"
    REQUEST_ACCESS = "request_access"
    RESOLVE_OUTPUT_CONFLICT = "resolve_output_conflict"
    RETRY = "retry"
    CHECK_INSTALLATION = "check_installation"
    HANDLE_MANUALLY = "handle_manually"
    REPORT_FAILURE = "report_failure"
    NONE = "none"


class ErrorCode(str, Enum):
    """Why a request or payload was rejected before any job existed."""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    UNKNOWN_POLICY = "UNKNOWN_POLICY"
    UNSUPPORTED_POLICY_VERSION = "UNSUPPORTED_POLICY_VERSION"
    INVALID_RESULT = "INVALID_RESULT"
    """A result violates the contract; always a CHONK defect, never user error."""


class ContractError(ValueError):
    """A payload violates the versioned contract.

    ``field`` is a dotted path such as ``checks.size_ceiling``. Messages name
    fields and limits but never echo submitted values, which may be sensitive.
    """

    def __init__(self, code: ErrorCode, message: str, *, field: str | None = None):
        self.code = code
        self.field = field
        self.message = message
        super().__init__(f"{code.value}: {message}" + (f" ({field})" if field else ""))


@dataclass(frozen=True)
class PrepareRequest:
    """Request to prepare one granted file under an exact byte ceiling.

    ``file_id`` is an opaque handle; resolving it to a local file is the job of
    the grant layer, not of the contract. ``target_bytes`` is an exact positive
    integer; human sizes are parsed before a request is built.
    """

    file_id: str
    target_bytes: int
    policy_id: str
    deadline_seconds: int
    max_attempts: int = 16
    idempotency_key: str | None = None
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class SearchSummary:
    """Bounded-search evidence. ``smallest_tested_bytes`` is the minimum over
    candidates actually produced, not a claim about all possible outputs."""

    attempts: int
    max_attempts: int
    smallest_tested_bytes: int | None = None


@dataclass(frozen=True)
class ReviewDecision:
    """A person accepted these advisory checks for this exact candidate."""

    accepted_checks: tuple[CheckId, ...]


@dataclass(frozen=True)
class PreparationResult:
    """Versioned, content-free result for one file.

    Contains no paths, file names, document text, or hashes. Use
    :func:`chonk.contract.validate_result` (or build it through
    :func:`chonk.contract.result_from_dict`) before sending it anywhere.
    """

    job_id: str
    file_id: str
    status: ResultStatus
    policy_id: str
    target_bytes: int
    next_action: NextAction
    reason_codes: tuple[ReasonCode, ...] = ()
    checks: Mapping[CheckId, CheckState] = field(default_factory=dict)
    completion_basis: CompletionBasis | None = None
    output_bytes: int | None = None
    artifact_id: str | None = None
    search: SearchSummary | None = None
    review: ReviewDecision | None = None
    limitations: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION
