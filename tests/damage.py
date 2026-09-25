"""Deliberate output damage used to prove that evaluation checks work.

Each function takes PDF bytes and returns a damaged copy. They model the
regressions listed in docs/product/VALIDATION.md (structural damage, text
damage, local visual damage) so tests can show that the evaluation suite
detects them, and record which of them the baseline product misses.
"""

from __future__ import annotations

import io

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, RectangleObject

from corpus.pdfbuild import Document, Name, Stream, draw_image, jpeg_image


def _writer(data: bytes) -> PdfWriter:
    return PdfWriter(clone_from=PdfReader(io.BytesIO(data), strict=False))


def _bytes(writer: PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def reorder_pages(data: bytes, order: list[int]) -> bytes:
    """Rebuild the document with pages in ``order`` (0-based, repeats allowed)."""
    reader = PdfReader(io.BytesIO(data), strict=False)
    writer = PdfWriter()
    for index in order:
        writer.add_page(reader.pages[index])
    return _bytes(writer)


def drop_page(data: bytes, index: int) -> bytes:
    count = len(PdfReader(io.BytesIO(data), strict=False).pages)
    return reorder_pages(data, [i for i in range(count) if i != index])


def duplicate_page(data: bytes, index: int) -> bytes:
    count = len(PdfReader(io.BytesIO(data), strict=False).pages)
    order = list(range(count))
    order.insert(index + 1, index)
    return reorder_pages(data, order)


def swap_pages(data: bytes, first: int, second: int) -> bytes:
    count = len(PdfReader(io.BytesIO(data), strict=False).pages)
    order = list(range(count))
    order[first], order[second] = order[second], order[first]
    return reorder_pages(data, order)


def rotate_page(data: bytes, index: int, degrees: int = 90) -> bytes:
    writer = _writer(data)
    writer.pages[index].rotate(degrees)
    return _bytes(writer)


def resize_page(data: bytes, index: int, width: float, height: float) -> bytes:
    writer = _writer(data)
    page = writer.pages[index]
    box = RectangleObject([0, 0, width, height])
    page.mediabox = box
    page.cropbox = box
    return _bytes(writer)


def _replace_content(writer: PdfWriter, index: int, content: bytes) -> None:
    stream = DecodedStreamObject()
    stream.set_data(content)
    writer.pages[index].replace_contents(stream)


def blank_page(data: bytes, index: int) -> bytes:
    writer = _writer(data)
    _replace_content(writer, index, b"")
    return _bytes(writer)


def replace_text(data: bytes, index: int, old: str, new: str) -> bytes:
    """Change a text-showing string in a page's content stream."""
    writer = _writer(data)
    content = writer.pages[index].get_contents().get_data()
    needle = old.encode("latin-1")
    if needle not in content:
        raise ValueError(f"{old!r} not found in page {index} content")
    _replace_content(writer, index, content.replace(needle, new.encode("latin-1"), 1))
    return _bytes(writer)


def cover_region(
    data: bytes, index: int, rect: tuple[float, float, float, float], gray: float = 0.0
) -> bytes:
    """Paint an opaque box over part of a page, like an unwanted redaction."""
    writer = _writer(data)
    page = writer.pages[index]
    content = page.get_contents().get_data()
    x, y, w, h = rect
    _replace_content(writer, index, content + f"\nq {gray} g {x} {y} {w} {h} re f Q\n".encode())
    return _bytes(writer)


def strip_text_layer(data: bytes, dpi: int = 150) -> bytes:
    """Replace every page with a picture of itself: same look, no text."""
    source = pdfium.PdfDocument(data)
    source.init_forms()
    document = Document()
    kids = []
    pages_ref = document.reserve()
    for index in range(len(source)):
        page = source[index]
        width, height = page.get_size()
        bitmap = page.render(scale=dpi / 72, may_draw_forms=True, fill_color=(255, 255, 255, 255))
        image = bitmap.to_pil().convert("RGB")
        bitmap.close()
        page.close()
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        image_ref = jpeg_image(document, buffer.getvalue(), image.width, image.height, "RGB")
        content = document.add(Stream({}, draw_image("Im1", 0, 0, width, height)))
        kids.append(
            document.add(
                {
                    "Type": Name("Page"),
                    "Parent": pages_ref,
                    "MediaBox": [0, 0, width, height],
                    "Resources": {"XObject": {"Im1": image_ref}},
                    "Contents": content,
                }
            )
        )
    source.close()
    document.set(pages_ref, {"Type": Name("Pages"), "Kids": kids, "Count": len(kids)})
    root = document.add({"Type": Name("Catalog"), "Pages": pages_ref})
    return document.to_bytes(root, b"\0" * 16)


def remove_widget_appearances(data: bytes) -> bytes:
    """Drop form-field appearance streams and values, keeping the page."""
    writer = _writer(data)
    for page in writer.pages:
        for annotation in page.get("/Annots") or []:
            annotation = annotation.get_object()
            if annotation.get("/Subtype") == "/Widget":
                for key in ("/AP", "/V", "/AS"):
                    annotation.pop(NameObject(key), None)
    return _bytes(writer)


def truncate(data: bytes, fraction: float = 0.5) -> bytes:
    return data[: int(len(data) * fraction)]
