"""Compare OCR of the original and the result, locally, returning counts only.

Two engines are supported, both fully offline:

* Tesseract, via its executable, fed a PNG on stdin (no temporary files).
  Install it separately (for example ``brew install tesseract``,
  ``apt install tesseract-ocr``, or the UB Mannheim Windows installer).
* RapidOCR through ``rapidocr-onnxruntime``, a pip install whose wheel ships
  its ONNX models, so it does not download anything at run time.

What the numbers mean. OCR is noisy on marginal glyphs: two near-identical
renders can read differently. So only words the engine read *confidently* on
the original (confidence >= 90 of 100) are counted. ``chars_compared`` is the
number of characters in those words; ``mismatches`` is how many of those
characters belong to words that do not reappear, identically and in order,
in the OCR of the compressed page. Zero mismatches means every confidently
read word survived character for character. It is strong evidence, not proof:
it says nothing about text OCR could not read confidently, photographs, or
security features.
"""

from __future__ import annotations

import difflib
import io
import logging
import os
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Protocol

from PIL import Image

from . import quality

log = logging.getLogger("chonk.ocr")

OCR_DPI = 200


CONFIDENT = 90.0

Word = tuple[str, float]  # (text, confidence 0-100)


class OcrEngine(Protocol):
    name: str

    def words(self, image: Image.Image) -> list[Word]: ...


class TesseractEngine:
    name = "tesseract"

    def __init__(self, executable: str, language: str = "eng"):
        self.executable = executable
        self.language = language

    def words(self, image: Image.Image) -> list[Word]:
        buffer = io.BytesIO()
        image.convert("L").save(buffer, "PNG")
        environment = dict(os.environ, OMP_THREAD_LIMIT="1")
        completed = subprocess.run(
            [self.executable, "stdin", "stdout", "-l", self.language, "--psm", "3", "tsv"],
            input=buffer.getvalue(), capture_output=True, timeout=300, check=False, env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError("tesseract failed")
        words: list[Word] = []
        for line in completed.stdout.decode("utf-8", "replace").splitlines()[1:]:
            columns = line.split("\t")
            if len(columns) < 12 or columns[0] != "5":  # level 5 = word
                continue
            try:
                confidence = float(columns[10])
            except ValueError:
                continue
            words.append((columns[11], confidence))
        return words


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR  # optional dependency

        self._engine = RapidOCR()

    def words(self, image: Image.Image) -> list[Word]:
        import numpy as np

        result, _ = self._engine(np.asarray(image.convert("RGB")))
        words: list[Word] = []
        for _box, text, score in result or []:
            # RapidOCR scores whole lines; every word inherits its line's score.
            words += [(word, float(score) * 100) for word in str(text).split()]
        return words


def find_engine(preference: str | None = None) -> OcrEngine | None:
    """The first available engine; ``preference`` is 'tesseract' or 'rapidocr'."""
    preference = preference or os.environ.get("CHONK_OCR")
    order = ["tesseract", "rapidocr"]
    if preference in order:
        order.remove(preference)
        order.insert(0, preference)
    elif preference == "none":
        return None
    for name in order:
        try:
            if name == "tesseract":
                executable = os.environ.get("CHONK_TESSERACT") or shutil.which("tesseract")
                if executable:
                    return TesseractEngine(executable, os.environ.get("CHONK_OCR_LANG", "eng"))
            else:
                return RapidOcrEngine()
        except Exception as exc:
            log.info("OCR engine %s unavailable: %s", name, type(exc).__name__)
    return None


@dataclass(frozen=True)
class OcrComparison:
    pages_compared: int
    chars_compared: int
    mismatches: int


def normalise(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text) if not ch.isspace())


def compare_words(original: list[Word], compressed: list[Word]) -> tuple[int, int]:
    """(characters in confident original words, characters in those that changed)."""
    source = [(normalise(text), confidence) for text, confidence in original]
    source = [(text, confidence) for text, confidence in source if text]
    result = [normalise(text) for text, _ in compressed]
    result = [text for text in result if text]
    matched = [False] * len(source)
    matcher = difflib.SequenceMatcher(None, [t for t, _ in source], result, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            matched[block.a + offset] = True
    chars = mismatches = 0
    for (text, confidence), survived in zip(source, matched):
        if confidence < CONFIDENT:
            continue
        chars += len(text)
        if not survived:
            mismatches += len(text)
    return chars, mismatches


def compare(original_pdf: bytes, compressed_pdf: bytes, engine: OcrEngine,
            pages: Iterable[int], dpi: int = OCR_DPI) -> OcrComparison:
    """OCR the listed pages of both PDFs. Text never leaves this function."""
    compared = chars = mismatches = 0
    for index in pages:
        original = engine.words(quality.render_page(original_pdf, index, dpi, "RGB"))
        compressed = engine.words(quality.render_page(compressed_pdf, index, dpi, "RGB"))
        page_chars, page_mismatches = compare_words(original, compressed)
        compared += 1
        chars += page_chars
        mismatches += page_mismatches
    return OcrComparison(compared, chars, mismatches)
