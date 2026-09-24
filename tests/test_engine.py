from __future__ import annotations

import io
import zlib

import pdfs
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
)

from chonk import engine, errors, quality
from chonk.sizes import human_size, parse_size


def compress(data: bytes, target: int, **options) -> engine.Result:
    return engine.compress_bytes(data, engine.Options(target_bytes=target, **options))


# -- sizes --------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("200KB", 200_000), ("2 MB", 2_000_000), ("1.5MiB", 1_572_864), ("4.1MB", 4_100_000),
    ("1200000", 1_200_000), ("1200000B", 1_200_000), ("1kib", 1024), (500, 500),
])
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "5 parsecs", "0KB", "-1MB", "0.0001B", True])
def test_parse_size_rejects(text):
    with pytest.raises(ValueError):
        parse_size(text)


def test_human_size():
    assert human_size(512) == "512 bytes"
    assert human_size(2048).startswith("2.00 KiB")


# -- statuses -------------------------------------------------------------------

def test_already_fits_returns_source_unchanged(scanned):
    result = compress(scanned, len(scanned) + 1)
    assert result.status == "already_fits"
    assert result.output == scanned
    assert result.worst_page_ssim == 1.0


def test_fit_is_under_target_valid_and_same_page_count(scanned):
    target = 400_000
    result = compress(scanned, target)
    assert result.status == "fit"
    assert result.output_bytes <= target
    assert len(PdfReader(io.BytesIO(result.output)).pages) == 2
    assert 0.9 < result.worst_page_ssim <= 1.0
    assert result.worst_page_ssim <= result.mean_ssim
    assert result.images_reencoded == 2
    assert result.lost == ()


def test_higher_target_gives_equal_or_better_quality(scanned):
    small = compress(scanned, 250_000)
    large = compress(scanned, 700_000)
    assert small.status == large.status == "fit"
    assert large.worst_page_ssim >= small.worst_page_ssim


def test_infeasible_reports_smallest_and_no_output(scanned):
    result = compress(scanned, 5_000)
    assert result.status == "infeasible"
    assert result.output is None
    assert result.smallest_bytes > 5_000


def test_grayscale_only_when_allowed_and_reported(scanned):
    colour_floor = compress(scanned, 1_000).smallest_bytes
    target = colour_floor - 1
    assert compress(scanned, target).status == "infeasible"
    result = compress(scanned, target, allow_grayscale=True)
    assert result.status == "fit"
    assert "color" in result.lost
    assert result.profile.grayscale


def test_text_only_pdf_is_compressed_losslessly(text_only):
    result = compress(text_only, len(text_only) - 1)
    assert result.status == "fit"
    assert result.profile.lossless
    assert result.worst_page_ssim > 0.999


def test_text_only_pdf_that_cannot_shrink_enough_is_infeasible(text_only):
    assert compress(text_only, 1_000).status == "infeasible"


# -- metadata, signatures, masks ---------------------------------------------

def test_metadata_is_kept_by_default_and_stripped_on_request(scanned):
    kept = compress(scanned, 600_000)
    assert pdfs.SECRET.encode() in kept.output
    stripped = compress(scanned, 600_000, strip_metadata=True)
    assert pdfs.SECRET.encode() not in stripped.output
    assert "metadata" in stripped.lost


def test_signed_document_reports_signature_loss(scanned):
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(scanned)))
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({  # noqa: SLF001
        NameObject("/SigFlags"): NumberObject(3),
    })
    buffer = io.BytesIO()
    writer.write(buffer)
    result = compress(buffer.getvalue(), 400_000)
    assert "digital_signature" in result.lost


def _pdf_with_soft_mask() -> bytes:
    width, height = 900, 600
    rgb = pdfs.scan_page(width, height).tobytes()
    alpha = bytes(255 if (x // 60 + y // 60) % 2 else 90 for y in range(height) for x in range(width))
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    mask = StreamObject()
    mask.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                 NameObject("/Width"): NumberObject(width), NameObject("/Height"): NumberObject(height),
                 NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                 NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Filter"): NameObject("/FlateDecode")})
    mask._data = zlib.compress(alpha)  # noqa: SLF001
    mask_ref = writer._add_object(mask)  # noqa: SLF001
    image = StreamObject()
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(width), NameObject("/Height"): NumberObject(height),
                  NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                  NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Filter"): NameObject("/FlateDecode"),
                  NameObject("/SMask"): mask_ref})
    image._data = zlib.compress(rgb, 1)  # noqa: SLF001
    image_ref = writer._add_object(image)  # noqa: SLF001
    content = StreamObject()
    content._data = b"q 540 0 0 360 36 400 cm /Im0 Do Q"  # noqa: SLF001
    page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image_ref})})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_soft_mask_survives_reencoding():
    source = _pdf_with_soft_mask()
    result = compress(source, len(source) // 4)
    assert result.status == "fit"
    assert result.images_reencoded == 1
    page = PdfReader(io.BytesIO(result.output)).pages[0]
    image = page["/Resources"]["/XObject"]["/Im0"].get_object()
    assert image["/Filter"] == "/DCTDecode"
    smask = image["/SMask"].get_object()
    assert smask["/Width"] == 900  # the untouched mask keeps its own size


def test_cmyk_images_are_left_alone():
    source = pdfs.cmyk_pdf()
    result = compress(source, len(source) // 3)
    assert result.status == "infeasible"
    assert result.images_total == 1


# -- errors carry fixed text only ---------------------------------------------

FIXED_MESSAGES = {cls.message for cls in (errors.NotPdfError, errors.EncryptedPdfError,
                                           errors.UnreadablePdfError, errors.EmptyPdfError)}


@pytest.mark.parametrize("data, code", [
    (b"hello world", "error:not_pdf"),
    (b"%PDF-1.7\n" + pdfs.SECRET.encode() * 10, "error:unreadable"),
])
def test_bad_inputs_map_to_codes(data, code):
    with pytest.raises(errors.ChonkError) as caught:
        compress(data, 1000)
    assert caught.value.code == code
    assert str(caught.value) in FIXED_MESSAGES
    assert pdfs.SECRET not in str(caught.value)


def test_encrypted_is_refused():
    with pytest.raises(errors.EncryptedPdfError):
        compress(pdfs.encrypted_pdf(), 1000)


def test_too_large_is_refused(scanned):
    with pytest.raises(errors.TooLargeError):
        engine.compress_bytes(scanned, engine.Options(target_bytes=1000, max_input_bytes=1000))


def test_inspect(scanned, text_only):
    info = engine.inspect(scanned)
    assert (info.pages, info.kind, info.signed) == (2, "scanned_images", False)
    assert engine.inspect(text_only).kind == "text_or_vector"


# -- quality measure ---------------------------------------------------------------

def test_ssim_properties():
    import numpy as np

    rng = np.random.default_rng(0)
    image = np.tile(np.linspace(0, 255, 80, dtype=np.uint8), (64, 1))  # smooth: noise is visible
    assert quality.ssim(image, image) == pytest.approx(1.0)
    noisy = np.clip(image.astype(int) + rng.integers(-40, 40, size=image.shape), 0, 255).astype(np.uint8)
    assert quality.ssim(image, noisy) < 0.5
    assert quality.ssim(image, image[:60, :70]) < 1.0  # size mismatch is padded, not ignored
