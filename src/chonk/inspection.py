"""Source PDF inspection before any rewrite."""

from __future__ import annotations

from pathlib import Path

from chonk.models import CompressionError

try:
    from pypdf import PdfReader
except ImportError:  # A friendly error is reported when the reader is needed.
    PdfReader = None  # type: ignore[assignment]


def pdf_page_count(path: Path) -> int:
    if PdfReader is None:
        raise CompressionError(
            "pypdf is required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )
    try:
        with path.open("rb") as stream:
            document = PdfReader(stream, strict=False)
            if document.is_encrypted:
                raise CompressionError(
                    "This PDF is password-protected. Unlock it before compressing."
                )
            page_count = len(document.pages)
            if page_count < 1:
                raise CompressionError("The input PDF has no pages.")
            return page_count
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Could not read input PDF: {exc}") from exc
