"""Structural checks on a compressed PDF."""

from __future__ import annotations

from pathlib import Path

from chonk.models import CompressionError

try:
    from pypdf import PdfReader
except ImportError:  # A friendly error is reported when the reader is needed.
    PdfReader = None  # type: ignore[assignment]


def validate_pdf(path: Path, expected_pages: int) -> None:
    if PdfReader is None:
        raise CompressionError("pypdf is required to validate the compressed PDF.")
    try:
        with path.open("rb") as stream:
            document = PdfReader(stream, strict=False)
            if document.is_encrypted:
                raise CompressionError("Ghostscript produced an encrypted output PDF.")
            page_count = len(document.pages)
            if page_count != expected_pages:
                raise CompressionError(
                    "The compressed PDF page count changed "
                    f"({expected_pages} input pages, {page_count} output pages)."
                )
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Ghostscript produced an unreadable PDF: {exc}") from exc
