"""Synthetic PDFs for tests. No real documents are used anywhere in the suite."""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

# A canary string. Tests assert it never appears in anything CHONK returns.
SECRET = "ZX9CANARY4417"


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def scan_page(width: int = 1654, height: int = 2339, seed: int = 0, text: str = SECRET) -> Image.Image:
    """An A4-ish 200 dpi 'scan': paper noise, a photo-like block, and text."""
    rng = random.Random(seed)
    image = Image.new("RGB", (width, height), (246, 244, 238))
    draw = ImageDraw.Draw(image)
    for _ in range(1400):
        x, y = rng.randrange(width), rng.randrange(height)
        r = rng.randrange(2, 30)
        shade = rng.randrange(170, 245)
        draw.ellipse((x, y, x + r, y + r), fill=(shade, shade - 8, shade - 20))
    draw.rectangle((120, 160, 620, 760), fill=(120, 90, 70))
    for i in range(0, 500, 7):
        draw.line((120 + i, 160, 620 - i // 2, 760), fill=(200 - i // 4, 150, 110 + i // 5), width=3)
    font = _font(56)
    small = _font(34)
    draw.text((700, 200), text, fill=(20, 20, 30), font=font)
    for line in range(24):
        words = " ".join(f"{text[:4]}{rng.randrange(10**5):05d}" for _ in range(5))
        draw.text((140, 860 + line * 56), words, fill=(25, 25, 35), font=small)
    return image.filter(ImageFilter.GaussianBlur(0.6))


def scanned_pdf(pages: int = 2, width: int = 1654, height: int = 2339, dpi: int = 200,
                quality: int = 95, title: str | None = None) -> bytes:
    images = [scan_page(width, height, seed=i) for i in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", resolution=dpi, save_all=True, append_images=images[1:], quality=quality)
    data = buffer.getvalue()
    if title is None:
        return data
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.add_metadata({"/Title": title, "/Author": title})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def text_pdf(text: str = SECRET, pages: int = 1) -> bytes:
    """A born-digital PDF: Helvetica text only, no images."""
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)  # noqa: SLF001
    for index in range(pages):
        page = writer.add_blank_page(612, 792)
        stream = DecodedStreamObject()
        lines = "".join(f"BT /F1 14 Tf 72 {700 - 20 * i} Td ({text} {index}-{i}) Tj ET\n" for i in range(30))
        stream.set_data(lines.encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def encrypted_pdf() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(text_pdf())))
    writer.encrypt(user_password="pw", owner_password="owner", algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def cmyk_pdf() -> bytes:
    image = scan_page(800, 1000).convert("CMYK")
    buffer = io.BytesIO()
    image.save(buffer, "PDF", resolution=100, quality=95)
    return buffer.getvalue()


__all__ = ["SECRET", "scanned_pdf", "text_pdf", "encrypted_pdf", "cmyk_pdf", "scan_page",
           "ArrayObject", "NumberObject"]
