"""The compared methods. Each takes (pdf bytes, target bytes) and returns bytes or None."""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

from chonk import engine


def _gs(source: bytes, extra: list[str]) -> bytes | None:
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp, "in.pdf"), Path(tmp, "out.pdf")
        src.write_bytes(source)
        completed = subprocess.run(
            ["gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", "-dNOPAUSE", "-dBATCH", "-dQUIET",
             "-dSAFER", *extra, f"-sOutputFile={dst}", str(src)],
            capture_output=True, timeout=900, check=False)
        if completed.returncode != 0 or not dst.is_file():
            return None
        return dst.read_bytes()


def gs_preset(preset: str):
    def run(source: bytes, target: int) -> bytes | None:
        return _gs(source, [f"-dPDFSETTINGS=/{preset}"])
    run.__name__ = f"gs_{preset}"
    return run


def _gs_screen_at(dpi: int) -> list[str]:
    return ["-dPDFSETTINGS=/screen", "-dDownsampleColorImages=true", "-dDownsampleGrayImages=true",
            f"-dColorImageResolution={dpi}", f"-dGrayImageResolution={dpi}", f"-dMonoImageResolution={dpi}",
            "-dColorImageDownsampleThreshold=1.0", "-dGrayImageDownsampleThreshold=1.0"]


LADDER = [["-dPDFSETTINGS=/printer"], ["-dPDFSETTINGS=/ebook"], ["-dPDFSETTINGS=/screen"],
          _gs_screen_at(50), _gs_screen_at(36)]


def gs_ladder(source: bytes, target: int) -> bytes | None:
    """Try harder presets until one fits; return the first that does."""
    last = None
    for step in LADDER:
        out = _gs(source, step)
        if out is not None:
            last = out
            if len(out) <= target:
                return out
    return last  # nothing fit: return the smallest attempt (it will count as not fitting)


def _raster_pdf(pages, dpi: int, quality: int) -> bytes:
    buffer = io.BytesIO()
    pages[0].save(buffer, "PDF", resolution=dpi, save_all=True, append_images=pages[1:], quality=quality)
    return buffer.getvalue()


def _render_all(source: bytes, dpi: int):
    document = pdfium.PdfDocument(source)
    try:
        pages = []
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=dpi / 72, fill_color=(255, 255, 255, 255))
            pages.append(bitmap.to_pil().convert("RGB"))
            bitmap.close()
            page.close()
        return pages
    finally:
        document.close()


def raster_target(source: bytes, target: int) -> bytes | None:
    """Rasterise pages; per resolution, binary-search the highest JPEG quality that fits."""
    smallest = None
    for dpi in (150, 120, 100, 72, 50):
        pages = _render_all(source, dpi)
        low, high, best = 10, 95, None
        floor = _raster_pdf(pages, dpi, low)
        if smallest is None or len(floor) < len(smallest):
            smallest = floor
        if len(floor) > target:
            continue
        best = floor
        while low < high:
            middle = (low + high + 1) // 2
            candidate = _raster_pdf(pages, dpi, middle)
            if len(candidate) <= target:
                low, best = middle, candidate
            else:
                high = middle - 1
        return best
    return smallest


def chonk(source: bytes, target: int) -> bytes | None:
    result = engine.compress_bytes(source, engine.Options(target_bytes=target))
    return result.output  # None when infeasible


METHODS = {
    "gs_ebook": gs_preset("ebook"),
    "gs_screen": gs_preset("screen"),
    "gs_ladder": gs_ladder,
    "raster_target": raster_target,
    "chonk": chonk,
}
