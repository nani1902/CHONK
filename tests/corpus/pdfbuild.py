"""A minimal, deterministic PDF writer for synthetic test fixtures.

The corpus is generated rather than committed so that every fixture has a
readable provenance. This writer emits classic cross-reference tables and
uncompressed content streams; nothing in the output depends on the clock,
the random module's global state, or dictionary hash order.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Iterable


class Name(str):
    """A PDF name object, written as ``/Value``."""


@dataclass(frozen=True)
class Ref:
    number: int


@dataclass(frozen=True)
class Raw:
    """Bytes written verbatim; used for signature placeholders."""

    data: bytes


@dataclass
class Stream:
    entries: dict[str, Any]
    data: bytes


def _serialize_name(value: str) -> bytes:
    out = bytearray(b"/")
    for byte in value.encode("ascii"):
        if byte < 0x21 or byte > 0x7E or chr(byte) in "#()<>[]{}/%":
            out += f"#{byte:02X}".encode("ascii")
        else:
            out.append(byte)
    return bytes(out)


def _serialize_string(value: str) -> bytes:
    raw = value.encode("latin-1")
    escaped = (
        raw.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
        .replace(b"\r", b"\\r")
        .replace(b"\n", b"\\n")
    )
    return b"(" + escaped + b")"


def _serialize_number(value: float) -> bytes:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return (text if text not in ("", "-0") else "0").encode("ascii")


def serialize(value: Any) -> bytes:
    if isinstance(value, Raw):
        return value.data
    if isinstance(value, Ref):
        return f"{value.number} 0 R".encode("ascii")
    if isinstance(value, Name):
        return _serialize_name(value)
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if value is None:
        return b"null"
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float):
        return _serialize_number(value)
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, bytes):
        return b"<" + value.hex().upper().encode("ascii") + b">"
    if isinstance(value, (list, tuple)):
        return b"[" + b" ".join(serialize(item) for item in value) + b"]"
    if isinstance(value, dict):
        parts = [
            _serialize_name(key) + b" " + serialize(item) for key, item in value.items()
        ]
        return b"<< " + b" ".join(parts) + b" >>"
    raise TypeError(f"cannot serialize {type(value).__name__}")


@dataclass
class Document:
    """An object table that serializes to a single-revision PDF file."""

    objects: list[Any] = field(default_factory=list)

    def reserve(self) -> Ref:
        self.objects.append(None)
        return Ref(len(self.objects))

    def add(self, value: Any) -> Ref:
        self.objects.append(value)
        return Ref(len(self.objects))

    def set(self, ref: Ref, value: Any) -> None:
        self.objects[ref.number - 1] = value

    def to_bytes(self, root: Ref, file_id: bytes, info: Ref | None = None) -> bytes:
        buffer = io.BytesIO()
        buffer.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for number, value in enumerate(self.objects, start=1):
            if value is None:
                raise ValueError(f"object {number} was reserved but never set")
            offsets.append(buffer.tell())
            buffer.write(f"{number} 0 obj\n".encode("ascii"))
            if isinstance(value, Stream):
                entries = dict(value.entries)
                entries["Length"] = len(value.data)
                buffer.write(serialize(entries))
                buffer.write(b"\nstream\n")
                buffer.write(value.data)
                buffer.write(b"\nendstream")
            else:
                buffer.write(serialize(value))
            buffer.write(b"\nendobj\n")

        xref_offset = buffer.tell()
        buffer.write(f"xref\n0 {len(self.objects) + 1}\n".encode("ascii"))
        buffer.write(b"0000000000 65535 f \n")
        for offset in offsets:
            buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
        trailer: dict[str, Any] = {
            "Size": len(self.objects) + 1,
            "Root": root,
            "ID": [file_id, file_id],
        }
        if info is not None:
            trailer["Info"] = info
        buffer.write(b"trailer\n" + serialize(trailer) + b"\n")
        buffer.write(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
        return buffer.getvalue()


# Page sizes in PDF points.
LETTER = (612, 792)
A4 = (595.276, 841.89)
LETTER_LANDSCAPE = (792, 612)

STANDARD_FONTS = {
    "F1": "Helvetica",
    "F2": "Courier",
    "F3": "Times-Roman",
    "F4": "Helvetica-Bold",
}


def font_resources(document: Document) -> dict[str, Ref]:
    return {
        key: document.add(
            {
                "Type": Name("Font"),
                "Subtype": Name("Type1"),
                "BaseFont": Name(base_font),
                "Encoding": Name("WinAnsiEncoding"),
            }
        )
        for key, base_font in STANDARD_FONTS.items()
    }


@dataclass(frozen=True)
class TextLine:
    x: float
    y: float
    size: float
    text: str
    font: str = "F1"


def text_operators(lines: Iterable[TextLine]) -> bytes:
    out = bytearray()
    for line in lines:
        out += b"BT /" + line.font.encode("ascii") + b" "
        out += _serialize_number(line.size) + b" Tf "
        out += _serialize_number(line.x) + b" " + _serialize_number(line.y) + b" Td "
        out += _serialize_string(line.text) + b" Tj ET\n"
    return bytes(out)


def jpeg_image(document: Document, jpeg: bytes, width: int, height: int, mode: str) -> Ref:
    color_space = {"L": "DeviceGray", "RGB": "DeviceRGB"}[mode]
    return document.add(
        Stream(
            {
                "Type": Name("XObject"),
                "Subtype": Name("Image"),
                "Width": width,
                "Height": height,
                "ColorSpace": Name(color_space),
                "BitsPerComponent": 8,
                "Filter": Name("DCTDecode"),
            },
            jpeg,
        )
    )


def draw_image(name: str, x: float, y: float, width: float, height: float) -> bytes:
    return (
        b"q "
        + b" ".join(
            _serialize_number(v) for v in (width, 0.0, 0.0, height, x, y)
        )
        + b" cm /"
        + name.encode("ascii")
        + b" Do Q\n"
    )


def info_dictionary(document: Document, title: str) -> Ref:
    # Fixed dates keep output reproducible; the Producer names the generator.
    return document.add(
        {
            "Title": title,
            "Producer": "CHONK synthetic test corpus",
            "Creator": "tests/corpus",
            "CreationDate": "D:20260101000000Z",
            "ModDate": "D:20260101000000Z",
        }
    )
