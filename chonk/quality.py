"""Render pages locally and measure how much a compressed page changed.

The measure is SSIM (Wang, Bovik, Sheikh and Simoncelli, "Image quality
assessment: from error visibility to structural similarity", IEEE Transactions
on Image Processing 13(4), 2004) on the luminance channel, with the paper's
11x11 Gaussian window (sigma 1.5), K1 = 0.01 and K2 = 0.03. CHONK reports the
worst page rather than the mean, because averaging hides one ruined page in a
long document.

Nothing in this module writes to disk or returns pixels to a caller outside
CHONK's own process.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

# Keep a single rendered page to about 12 megapixels, whatever the page size.
MAX_RENDER_PIXELS = 12_000_000
# Stop caching source renders past this many bytes; they are re-rendered instead.
REFERENCE_CACHE_BYTES = 256 * 1024 * 1024

_K1, _K2, _L = 0.01, 0.03, 255.0
_C1, _C2 = (_K1 * _L) ** 2, (_K2 * _L) ** 2


def _gaussian_kernel(size: int = 11, sigma: float = 1.5) -> np.ndarray:
    offsets = np.arange(size, dtype=np.float64) - (size - 1) / 2
    kernel = np.exp(-(offsets**2) / (2 * sigma**2))
    return (kernel / kernel.sum()).astype(np.float32)


_KERNEL = _gaussian_kernel()


def _filter(image: np.ndarray) -> np.ndarray:
    """Separable 'valid' Gaussian filter."""
    k = _KERNEL
    n = len(k)
    height, width = image.shape
    rows = np.zeros((height, width - n + 1), dtype=np.float32)
    for i in range(n):
        rows += k[i] * image[:, i : width - n + 1 + i]
    out = np.zeros((height - n + 1, width - n + 1), dtype=np.float32)
    for i in range(n):
        out += k[i] * rows[i : height - n + 1 + i, :]
    return out


def ssim(first: np.ndarray, second: np.ndarray) -> float:
    """Mean SSIM of two 8-bit grayscale images; pads the smaller with white."""
    if first.shape != second.shape:
        height = max(first.shape[0], second.shape[0])
        width = max(first.shape[1], second.shape[1])
        first = _pad_white(first, height, width)
        second = _pad_white(second, height, width)
    if min(first.shape) < len(_KERNEL):
        return 1.0 if np.array_equal(first, second) else 0.0

    x = first.astype(np.float32)
    y = second.astype(np.float32)
    mu_x = _filter(x)
    mu_y = _filter(y)
    mu_x2 = mu_x * mu_x
    mu_y2 = mu_y * mu_y
    mu_xy = mu_x * mu_y
    sigma_x2 = _filter(x * x) - mu_x2
    sigma_y2 = _filter(y * y) - mu_y2
    sigma_xy = _filter(x * y) - mu_xy
    numerator = (2 * mu_xy + _C1) * (2 * sigma_xy + _C2)
    denominator = (mu_x2 + mu_y2 + _C1) * (sigma_x2 + sigma_y2 + _C2)
    return float(np.mean(numerator / denominator))


def _pad_white(image: np.ndarray, height: int, width: int) -> np.ndarray:
    if image.shape == (height, width):
        return image
    canvas = np.full((height, width), 255, dtype=image.dtype)
    canvas[: image.shape[0], : image.shape[1]] = image
    return canvas


def _render_scale(page, dpi: int) -> float:
    width, height = page.get_size()
    scale = dpi / 72
    pixels = width * scale * height * scale
    if pixels > MAX_RENDER_PIXELS:
        scale *= (MAX_RENDER_PIXELS / pixels) ** 0.5
    return scale


def _render(page, dpi: int, mode: str) -> Image.Image:
    bitmap = page.render(
        scale=_render_scale(page, dpi),
        draw_annots=True,
        fill_color=(255, 255, 255, 255),
    )
    try:
        return bitmap.to_pil().convert(mode)
    finally:
        bitmap.close()


def page_count(pdf: bytes) -> int:
    document = pdfium.PdfDocument(pdf)
    try:
        return len(document)
    finally:
        document.close()


def render_pages(pdf: bytes, dpi: int, mode: str = "L") -> Iterator[Image.Image]:
    """Yield each page rendered at ``dpi`` as a PIL image, in memory."""
    document = pdfium.PdfDocument(pdf)
    try:
        for index in range(len(document)):
            page = document[index]
            try:
                yield _render(page, dpi, mode)
            finally:
                page.close()
    finally:
        document.close()


def render_page(pdf: bytes, index: int, dpi: int, mode: str = "RGB") -> Image.Image:
    document = pdfium.PdfDocument(pdf)
    try:
        page = document[index]
        try:
            return _render(page, dpi, mode)
        finally:
            page.close()
    finally:
        document.close()


def render_page_png(pdf: bytes, index: int, dpi: int) -> bytes:
    buffer = io.BytesIO()
    render_page(pdf, index, dpi, "RGB").save(buffer, "PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class PageScores:
    scores: tuple[float, ...]

    @property
    def worst(self) -> float:
        return min(self.scores)

    @property
    def worst_index(self) -> int:
        return self.scores.index(self.worst)

    @property
    def mean(self) -> float:
        return sum(self.scores) / len(self.scores)


class Reference:
    """Source renders, cached in memory, compared against candidates."""

    def __init__(self, source: bytes, dpi: int = 150):
        self.source = source
        self.dpi = dpi
        self._cache: dict[int, np.ndarray] = {}
        self._cached_bytes = 0

    def _source_page(self, document, index: int) -> np.ndarray:
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        page = document[index]
        try:
            array = np.asarray(_render(page, self.dpi, "L"), dtype=np.uint8)
        finally:
            page.close()
        if self._cached_bytes + array.nbytes <= REFERENCE_CACHE_BYTES:
            self._cache[index] = array
            self._cached_bytes += array.nbytes
        return array

    def compare(self, candidate: bytes) -> PageScores:
        source_doc = pdfium.PdfDocument(self.source)
        candidate_doc = pdfium.PdfDocument(candidate)
        try:
            if len(source_doc) != len(candidate_doc):
                raise ValueError("page count changed")
            scores = []
            for index in range(len(candidate_doc)):
                original = self._source_page(source_doc, index)
                page = candidate_doc[index]
                try:
                    compressed = np.asarray(_render(page, self.dpi, "L"), dtype=np.uint8)
                finally:
                    page.close()
                scores.append(ssim(original, compressed))
            return PageScores(tuple(scores))
        finally:
            candidate_doc.close()
            source_doc.close()
