"""Rendered-page comparison between a source and a candidate."""

from __future__ import annotations

from pathlib import Path

from chonk.models import CompressionError

try:
    import pypdfium2 as pdfium
except ImportError:  # A friendly error is reported when rendering is needed.
    pdfium = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # A friendly error is reported when rendering is needed.
    Image = ImageChops = ImageStat = None  # type: ignore[assignment]


def render_page(page, dpi: int):
    bitmap = page.render(
        scale=dpi / 72,
        draw_annots=True,
        fill_color=(255, 255, 255, 255),
        rev_byteorder=True,
    )
    try:
        return bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()


def compare_visual_similarity(
    source_path: Path,
    candidate_path: Path,
    dpi: int,
) -> float:
    """Return mean per-page pixel similarity, from 0 (different) to 1 (same)."""
    if pdfium is None or Image is None or ImageChops is None or ImageStat is None:
        raise CompressionError(
            "pypdfium2 and Pillow are required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )

    page_scores: list[float] = []
    try:
        with pdfium.PdfDocument(str(source_path)) as source, pdfium.PdfDocument(
            str(candidate_path)
        ) as document:
            if len(document) != len(source):
                raise CompressionError("Page count changed during visual comparison.")
            for page_number in range(len(document)):
                source_page = source[page_number]
                candidate_page = document[page_number]
                try:
                    original = render_page(source_page, dpi)
                    compressed = render_page(candidate_page, dpi)
                finally:
                    source_page.close()
                    candidate_page.close()

                width = max(original.width, compressed.width)
                height = max(original.height, compressed.height)
                original_canvas = Image.new("RGB", (width, height), "white")
                compressed_canvas = Image.new("RGB", (width, height), "white")
                original_canvas.paste(original, (0, 0))
                compressed_canvas.paste(compressed, (0, 0))

                channel_errors = ImageStat.Stat(
                    ImageChops.difference(original_canvas, compressed_canvas)
                ).rms
                pixel_error = sum(channel_errors) / (3 * 255)
                geometry_score = (
                    min(original.width, compressed.width) / width
                ) * (min(original.height, compressed.height) / height)
                page_scores.append(max(0.0, 1.0 - pixel_error) * geometry_score)
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Could not compare a compressed page render: {exc}") from exc

    if not page_scores:
        raise CompressionError("Could not compare an empty PDF.")
    return sum(page_scores) / len(page_scores)
