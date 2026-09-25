"""Independent evaluation checks for comparing a source PDF with an output.

These checks are the test suite's oracle. They deliberately do not reuse
``pdf_compressor``'s own validation so that gaps in the product's checks are
visible instead of being tested against themselves. They detect gross
regressions (missing, reordered, resized, blanked, or altered pages; lost
text layers, form values, or signatures). They are not a calibrated quality
model and make no readability claim; that is CHONK-009's job.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageChops
from pypdf import PdfReader

RENDER_DPI = 50
TILE_PIXELS = 8  # about 11.5 pt at 50 dpi, roughly one line of body text
# Worst-tile similarity below this is treated as gross visual damage. Chosen
# from the synthetic corpus with Ghostscript 10.02: legitimate output at the
# most aggressive ladder profile scored >= 0.93, while the damage in
# tests/damage.py scored 0.16-0.75 (blanked, rotated, resized, or obscured
# pages; stripped form appearances). It is a gross-damage detector, not a
# readability threshold. tests/unit/test_evaluation.py re-checks the damage
# side and tests/integration/test_cli_ghostscript.py the legitimate side.
VISUAL_FLOOR = 0.80
GEOMETRY_TOLERANCE_POINTS = 0.5


@dataclass(frozen=True)
class Finding:
    check: str
    page: int | None
    detail: str


@dataclass(frozen=True)
class Evaluation:
    findings: tuple[Finding, ...]
    page_similarity: tuple[float, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def failed_checks(self) -> set[str]:
        return {finding.check for finding in self.findings}


def _reader(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data), strict=False)


def normalized_text(text: str) -> str:
    """Remove whitespace only; digits, punctuation, and case are kept.

    Text extractors disagree about spaces between rewritten text runs, so
    whitespace differences are not treated as damage. This cannot detect a
    change that only moves a space (for example "12 3" versus "1 23").
    """
    return re.sub(r"\s+", "", text)


def page_texts(data: bytes) -> list[str]:
    return [normalized_text(page.extract_text() or "") for page in _reader(data).pages]


def page_geometry(data: bytes) -> list[tuple[float, float]]:
    """Displayed page size: MediaBox width and height after /Rotate.

    Ghostscript folds /Rotate into the MediaBox, which does not change what a
    reader sees, so raw attributes are not compared.
    """
    geometry = []
    for page in _reader(data).pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if page.rotation % 180:
            width, height = height, width
        geometry.append((width, height))
    return geometry


def annotation_subtypes(data: bytes) -> list[list[str]]:
    """Per page, the sorted non-widget annotation subtypes."""
    pages = []
    for page in _reader(data).pages:
        subtypes = [
            str(annotation.get_object().get("/Subtype"))
            for annotation in page.get("/Annots") or []
        ]
        pages.append(sorted(s for s in subtypes if s != "/Widget"))
    return pages


def outline_count(data: bytes) -> int:
    return len(_reader(data).outline)


def form_fields(data: bytes) -> dict[str, str]:
    fields = _reader(data).get_fields() or {}
    return {
        name: str(field.get("/V"))
        for name, field in fields.items()
        if field.get("/FT") != "/Sig"
    }


def covering_signatures(data: bytes) -> int:
    """Count signatures whose /ByteRange spans the whole file except /Contents.

    Covering the full file is necessary (not sufficient) for a signature to
    remain valid; any rewrite by Ghostscript breaks it.
    """
    count = 0
    for field in (_reader(data).get_fields() or {}).values():
        if field.get("/FT") != "/Sig":
            continue
        value = field.get("/V")
        value = value.get_object() if value is not None else None
        byte_range = value.get("/ByteRange") if value is not None else None
        if byte_range and len(byte_range) == 4:
            start, first, second, rest = (int(item) for item in byte_range)
            if start == 0 and second + rest == len(data) and first < second:
                count += 1
    return count


def render_pages(data: bytes, dpi: int = RENDER_DPI) -> list[Image.Image]:
    document = pdfium.PdfDocument(data)
    try:
        document.init_forms()  # without this pdfium skips widget appearances
        images = []
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(
                    scale=dpi / 72, may_draw_forms=True, fill_color=(255, 255, 255, 255)
                )
                images.append(bitmap.to_pil().convert("L"))
                bitmap.close()
            finally:
                page.close()
        return images
    finally:
        document.close()


def worst_tile_similarity(source: Image.Image, output: Image.Image) -> float:
    if output.size != source.size:
        output = output.resize(source.size, Image.Resampling.BILINEAR)
    difference = ImageChops.difference(source, output)
    tiles = (
        max(1, source.width // TILE_PIXELS),
        max(1, source.height // TILE_PIXELS),
    )
    tile_means = difference.resize(tiles, Image.Resampling.BOX)
    return 1.0 - tile_means.getextrema()[1] / 255


def evaluate(
    source: bytes,
    output: bytes,
    *,
    target_bytes: int | None = None,
    visual_floor: float = VISUAL_FLOOR,
) -> Evaluation:
    findings: list[Finding] = []

    if target_bytes is not None and len(output) > target_bytes:
        findings.append(
            Finding("size_ceiling", None, f"{len(output)} bytes > {target_bytes} bytes")
        )

    try:
        output_geometry = page_geometry(output)
        output_texts = page_texts(output)
        output_renders = render_pages(output)
    except Exception as exc:  # any parse/render failure is a finding
        findings.append(Finding("parse", None, f"output unreadable: {type(exc).__name__}"))
        return Evaluation(tuple(findings), ())

    source_geometry = page_geometry(source)
    if len(output_geometry) != len(source_geometry):
        findings.append(
            Finding(
                "page_count",
                None,
                f"{len(source_geometry)} source pages, {len(output_geometry)} output pages",
            )
        )
        return Evaluation(tuple(findings), ())

    for number, (before, after) in enumerate(zip(source_geometry, output_geometry), 1):
        if (
            abs(before[0] - after[0]) > GEOMETRY_TOLERANCE_POINTS
            or abs(before[1] - after[1]) > GEOMETRY_TOLERANCE_POINTS
        ):
            findings.append(Finding("page_geometry", number, f"{before} -> {after}"))

    for number, (before, after) in enumerate(zip(page_texts(source), output_texts), 1):
        if not before:
            continue  # no text layer to preserve on this page
        if not after:
            findings.append(Finding("text", number, "text layer missing"))
        elif before != after:
            position = next(
                (i for i, (a, b) in enumerate(zip(before, after)) if a != b),
                min(len(before), len(after)),
            )
            findings.append(Finding("text", number, f"text differs at character {position}"))

    for number, (before, after) in enumerate(
        zip(annotation_subtypes(source), annotation_subtypes(output)), 1
    ):
        if before != after:
            findings.append(Finding("annotations", number, f"{before} -> {after}"))

    if outline_count(output) < outline_count(source):
        findings.append(Finding("outline", None, "outline entries missing"))

    source_fields = form_fields(source)
    output_fields = form_fields(output)
    for name, value in source_fields.items():
        if output_fields.get(name) != value:
            findings.append(
                Finding("form_fields", None, f"field {name!r} missing or changed")
            )

    expected_signatures = covering_signatures(source)
    if expected_signatures and covering_signatures(output) < expected_signatures:
        findings.append(Finding("signature", None, "signature no longer covers the file"))

    similarities = []
    for number, (before, after) in enumerate(zip(render_pages(source), output_renders), 1):
        similarity = worst_tile_similarity(before, after)
        similarities.append(similarity)
        if similarity < visual_floor:
            findings.append(
                Finding("visual", number, f"worst tile similarity {similarity:.3f}")
            )

    return Evaluation(tuple(findings), tuple(similarities))
