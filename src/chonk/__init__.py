"""CHONK: fit a PDF under a byte ceiling while keeping the clearest tested result.

The engine in :mod:`chonk.engine` is independent of any user interface. Callers
build a :class:`CompressionRequest`, optionally observe typed progress events,
and receive a :class:`CompressionResult`.
"""

from chonk.engine import compress_pdf, validate_request
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
    ProgressEvent,
    ResultStatus,
)
from chonk.sizes import human_size, parse_size

__version__ = "0.1.0"

__all__ = [
    "AttemptRecord",
    "AttemptStarted",
    "CandidateMeasured",
    "CompressionError",
    "CompressionRequest",
    "CompressionResult",
    "InputInspected",
    "InvalidRequestError",
    "OutputExistsError",
    "ProgressEvent",
    "ResultStatus",
    "__version__",
    "compress_pdf",
    "human_size",
    "parse_size",
    "validate_request",
]
