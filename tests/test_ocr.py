from __future__ import annotations

import io
import random

import pdfs
import pytest
from PIL import Image, ImageDraw

from chonk import engine, ocr


def test_identical_words_have_no_mismatches():
    words = [("PASSPORT", 96.0), ("L898902C3", 93.0)]
    assert ocr.compare_words(words, words) == (len("PASSPORT") + len("L898902C3"), 0)


def test_changed_confident_word_counts_all_its_characters():
    original = [("PASSPORT", 96.0), ("L898902C3", 93.0)]
    compressed = [("PASSPORT", 96.0), ("L898902G3", 70.0)]
    assert ocr.compare_words(original, compressed) == (17, 9)


def test_low_confidence_words_are_not_counted():
    original = [("smudge", 40.0), ("NAME", 95.0)]
    compressed = [("smuclge", 30.0), ("NAME", 95.0)]
    assert ocr.compare_words(original, compressed) == (4, 0)


def test_missing_word_is_a_mismatch_and_whitespace_is_ignored():
    original = [("DATE OF", 95.0), ("BIRTH", 95.0)]
    assert ocr.compare_words(original, [("DATEOF", 90.0)]) == (11, 5)


def _clean_text_pdf() -> bytes:
    rng = random.Random(0)
    image = Image.new("RGB", (1654, 1170), (250, 248, 242))
    draw = ImageDraw.Draw(image)
    words = "PASSPORT REPUBLIC SURNAME GIVEN NAMES NATIONALITY DATE BIRTH PLACE AUTHORITY".split()
    for line in range(10):
        draw.text((140, 100 + line * 90), " ".join(rng.choice(words) for _ in range(4)),
                  fill=(20, 20, 30), font=pdfs._font(44))
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=200, quality=95)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["tesseract", "rapidocr"])
def test_real_engine_sees_no_change_at_moderate_compression_and_catches_destruction(name):
    found = ocr.find_engine(name)
    if found is None or found.name != name:
        pytest.skip(f"{name} is not installed")
    source = _clean_text_pdf()
    fine = engine.compress_bytes(source, engine.Options(target_bytes=len(source) // 3))
    assert fine.status == "fit"
    result = ocr.compare(source, fine.output, found, [0])
    assert result.chars_compared > 100 and result.mismatches == 0

    # Force a destructive setting (40 dpi) that the search itself would never pick here.
    base = engine._lossless_pass(source, False)  # noqa: SLF001
    search = engine._Search(source, base, engine.ImageSet(base), engine.Options(target_bytes=10**9))  # noqa: SLF001
    ruined = search.materialize(search.build(engine.Profile(40, 30)))
    assert ocr.compare(source, ruined, found, [0]).mismatches > 0
