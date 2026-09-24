"""Synthetic benchmark documents. Fixed seeds; no real people, faces or IDs.

The passport-style page uses ICAO's fictional specimen state "UTO" (Utopia).
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject, StreamObject

FONTS = Path("/usr/share/fonts/truetype")
SANS = FONTS / "liberation" / "LiberationSans-Regular.ttf"
SANS_BOLD = FONTS / "liberation" / "LiberationSans-Bold.ttf"
SERIF = FONTS / "liberation" / "LiberationSerif-Regular.ttf"
MONO = FONTS / "dejavu" / "DejaVuSansMono.ttf"
DPI = 300
A4 = (2480, 3508)  # 210 x 297 mm at 300 dpi

SURNAMES = ["ERIKSSON", "OKAFOR", "HALVORSEN", "NAKAMURA", "FERREIRA", "LINDQVIST", "MOREAU", "KOWALSKI"]
GIVEN = ["ANNA MARIA", "DAVID", "PRIYA", "TOMAS", "LEILA", "JONAS", "MEERA", "SAMUEL"]
SUBJECTS = ["English Language", "Mathematics", "Physics", "Chemistry", "Biology", "History",
            "Geography", "Computer Science", "Economics", "Political Science", "Accountancy",
            "Physical Education"]
WORDS = ("the applicant certifies that all information provided in this application is true and "
         "complete to the best of their knowledge and that supporting documents are genuine "
         "copies of the original records held by the issuing authority").split()


def pt(size: float) -> int:
    """Font size in pixels at 300 dpi for a point size."""
    return round(size * DPI / 72)


def font(path: Path, size_pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), pt(size_pt))


def scanner_finish(image: Image.Image, seed: int, blur: float = 0.7, noise: float = 6.0) -> Image.Image:
    """Paper tint, slight optical blur and sensor noise, like a flatbed scan."""
    rng = np.random.default_rng(seed)
    image = image.filter(ImageFilter.GaussianBlur(blur))
    array = np.asarray(image).astype(np.float32)
    array += rng.normal(0, noise, array.shape)
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), image.mode)


def guilloche(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], seed: int) -> None:
    rng = random.Random(seed)
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    for family in range(3):
        colour = [(196, 214, 232), (230, 205, 214), (206, 228, 204)][family]
        freq = rng.uniform(3, 7)
        for k in range(0, 60):
            phase = k * 0.105 + family
            points = [(left + x, top + height / 2 + (height / 2.3) * math.sin(freq * x / width * 2 * math.pi + phase)
                       * math.cos(x / width * math.pi + k * 0.05)) for x in range(0, width, 6)]
            draw.line(points, fill=colour, width=2)


def portrait(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    """An abstract head-and-shoulders silhouette. Not a face."""
    left, top, right, bottom = box
    draw.rectangle(box, fill=(214, 222, 230))
    width, height = right - left, bottom - top
    cx = left + width // 2
    for i in range(40):
        shade = 180 - i * 2
        draw.ellipse((cx - width * 0.24 + i, top + height * 0.12 + i, cx + width * 0.24 - i,
                      top + height * 0.6 - i), fill=(shade + 40, shade + 10, shade - 10))
    for i in range(30):
        shade = 70 + i
        draw.pieslice((left + width * 0.05 + i, top + height * 0.62 + i, right - width * 0.05 - i,
                       bottom + height * 0.5), 180, 360, fill=(shade, shade + 5, shade + 20))


def mrz_lines(rng: random.Random) -> list[str]:
    surname, given = rng.choice(SURNAMES), rng.choice(GIVEN).replace(" ", "<")
    line1 = f"P<UTO{surname}<<{given}".ljust(44, "<")[:44]
    number = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(9))
    line2 = f"{number}{rng.randint(0, 9)}UTO{rng.randint(700101, 991231)}{rng.randint(0, 9)}F" \
            f"{rng.randint(270101, 361231)}{rng.randint(0, 9)}".ljust(42, "<") + f"{rng.randint(0, 9)}{rng.randint(0, 9)}"
    return [line1, line2[:44]]


def passport_page(seed: int) -> Image.Image:
    rng = random.Random(seed)
    page = Image.new("RGB", A4, (247, 246, 242))
    draw = ImageDraw.Draw(page)
    card = (180, 220, 180 + 1476, 220 + 1039)  # 125 x 88 mm
    draw.rounded_rectangle(card, radius=40, fill=(238, 240, 236), outline=(160, 160, 160), width=3)
    guilloche(draw, (card[0] + 20, card[1] + 20, card[2] - 20, card[3] - 20), seed)
    draw.text((card[0] + 60, card[1] + 40), "UTOPIA  ·  PASSPORT  ·  PASSEPORT", font=font(SANS_BOLD, 11), fill=(40, 50, 90))
    portrait(draw, (card[0] + 60, card[1] + 150, card[0] + 60 + 420, card[1] + 150 + 540))
    labels = [("Type / Type", "P"), ("Code / Code", "UTO"), ("Passport No.", f"L{rng.randint(10**7, 10**8 - 1)}"),
              ("Surname / Nom", rng.choice(SURNAMES)), ("Given names / Prénoms", rng.choice(GIVEN)),
              ("Nationality", "UTOPIAN"), ("Date of birth", f"{rng.randint(1, 28):02d} MAR {rng.randint(1960, 2004)}"),
              ("Place of birth", "ZENITH CITY"), ("Date of issue", f"{rng.randint(1, 28):02d} JAN 2024"),
              ("Date of expiry", f"{rng.randint(1, 28):02d} JAN 2034"), ("Authority", "MINISTRY OF THE INTERIOR")]
    x, y = card[0] + 540, card[1] + 140
    for index, (label, value) in enumerate(labels):
        column = index % 2
        row = index // 2
        lx, ly = x + column * 440, y + row * 118
        draw.text((lx, ly), label, font=font(SANS, 7), fill=(90, 90, 110))
        draw.text((lx, ly + 36), value, font=font(SANS_BOLD, 9), fill=(20, 20, 30))
    mono = font(MONO, 10)
    for index, line in enumerate(mrz_lines(rng)):
        draw.text((card[0] + 50, card[3] - 190 + index * 70), line, font=mono, fill=(15, 15, 20))
    # Lower half: a second, text-heavy page such as the observations page.
    body = font(SANS, 9)
    for line in range(22):
        words = " ".join(rng.choice(WORDS) for _ in range(12))
        draw.text((200, 1500 + line * 70), words.capitalize() + ".", font=body, fill=(30, 30, 40))
    return scanner_finish(page, seed)


def marksheet_page(seed: int) -> Image.Image:
    rng = random.Random(seed)
    page = Image.new("L", A4, 244)
    draw = ImageDraw.Draw(page)
    draw.text((300, 200), "BOARD OF SECONDARY EDUCATION, UTOPIA", font=font(SERIF, 16), fill=20)
    draw.text((300, 300), "STATEMENT OF MARKS  ·  ANNUAL EXAMINATION 2025", font=font(SANS_BOLD, 11), fill=30)
    info = font(SANS, 9)
    draw.text((300, 420), f"Name: {rng.choice(GIVEN)} {rng.choice(SURNAMES)}    Roll No: {rng.randint(10**6, 10**7)}",
              font=info, fill=25)
    draw.text((300, 480), f"School: Zenith City Public School    Centre: {rng.randint(1000, 9999)}", font=info, fill=25)
    small = font(SANS, 8)
    top, row_h = 620, 110
    columns = [300, 1300, 1600, 1900, 2180]
    for r in range(len(SUBJECTS) + 2):
        draw.line((300, top + r * row_h, 2180, top + r * row_h), fill=90, width=3)
    for c in columns:
        draw.line((c, top, c, top + (len(SUBJECTS) + 1) * row_h), fill=90, width=3)
    for c, heading in zip(columns, ("Subject", "Theory", "Practical", "Total")):
        draw.text((c + 20, top + 30), heading, font=font(SANS_BOLD, 8), fill=20)
    for r, subject in enumerate(SUBJECTS, start=1):
        theory, practical = rng.randint(35, 80), rng.randint(10, 20)
        cells = (subject, str(theory), str(practical), str(theory + practical))
        for c, value in zip(columns, cells):
            draw.text((c + 20, top + r * row_h + 32), value, font=small, fill=25)
    y = top + (len(SUBJECTS) + 2) * row_h
    for line in range(8):
        words = " ".join(rng.choice(WORDS) for _ in range(13))
        draw.text((300, y + line * 60), words.capitalize() + ".", font=small, fill=40)
    # Stamp and signature.
    draw.ellipse((1650, 2950, 2050, 3350), outline=110, width=10)
    draw.text((1720, 3120), "CONTROLLER", font=font(SANS_BOLD, 9), fill=110)
    points, px, py = [], 400.0, 3150.0
    for step in range(120):
        px += 6 + 3 * math.sin(step / 4)
        py += 14 * math.sin(step / 3 + rng.random())
        points.append((px, py))
    draw.line(points, fill=40, width=6)
    return scanner_finish(page, seed, noise=5)


def text_page_image(seed: int, size=(1654, 2339), point=10) -> Image.Image:
    """A printed page at 200 dpi, for photographing."""
    rng = random.Random(seed)
    page = Image.new("RGB", size, (250, 250, 247))
    draw = ImageDraw.Draw(page)
    scale = 200 / 300
    title = ImageFont.truetype(str(SANS_BOLD), round(pt(16) * scale))
    body = ImageFont.truetype(str(SERIF), round(pt(point) * scale))
    draw.text((130, 120), "RESIDENCE CERTIFICATE", font=title, fill=(20, 20, 20))
    for line in range(40):
        words = " ".join(rng.choice(WORDS) for _ in range(11))
        draw.text((130, 260 + line * 50), words.capitalize() + ".", font=body, fill=(25, 25, 25))
    return page


def phone_photo(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    width, height = 3000, 4000  # 12 MP portrait
    desk = np.zeros((height, width, 3), np.float32)
    grain = rng.normal(0, 1, (height // 8, width // 8)).repeat(8, 0).repeat(8, 1)
    desk[..., 0] = 120 + 25 * grain
    desk[..., 1] = 85 + 18 * grain
    desk[..., 2] = 55 + 12 * grain
    background = Image.fromarray(np.clip(desk, 0, 255).astype(np.uint8))
    page = text_page_image(seed)
    # Perspective: map the page onto a slightly skewed quadrilateral.
    quad = (260, 420, 180, 3700, 2850, 3760, 2760, 330)  # corners in the photo: UL, LL, LR, UR
    coefficients = _photo_to_page(quad, page.size)
    warped = page.transform((width, height), Image.Transform.PERSPECTIVE, data=coefficients,
                            resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0))
    mask = Image.new("L", page.size, 255).transform((width, height), Image.Transform.PERSPECTIVE,
                                                    data=coefficients)
    background.paste(warped, (0, 0), mask)
    # Uneven light and sensor noise.
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    light = 1.08 - 0.35 * (((xx - width * 0.3) / width) ** 2 + ((yy - height * 0.25) / height) ** 2)
    array = np.asarray(background).astype(np.float32) * light[..., None]
    array += rng.normal(0, 4, array.shape)
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))


def _photo_to_page(quad, size):
    """PERSPECTIVE coefficients mapping photo pixels back to page pixels, so the
    page's corners land on ``quad`` (UL, LL, LR, UR) in the photo."""
    ul, ll, lr, ur = [(quad[i], quad[i + 1]) for i in range(0, 8, 2)]
    w, h = size
    src = [ul, ll, lr, ur]
    dst = [(0, 0), (0, h), (w, h), (w, 0)]
    return _perspective_coeffs(src, dst)


def _perspective_coeffs(src, dst):
    matrix, vector = [], []
    for (x, y), (u, v) in zip(src, dst):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vector += [u, v]
    return tuple(np.linalg.solve(np.array(matrix, float), np.array(vector, float)))


def certificate_page(seed: int) -> Image.Image:
    rng = random.Random(seed)
    page = Image.new("RGB", A4, (250, 247, 236))
    draw = ImageDraw.Draw(page)
    for inset, colour in ((80, (120, 30, 40)), (110, (180, 140, 60)), (140, (120, 30, 40))):
        draw.rectangle((inset, inset, A4[0] - inset, A4[1] - inset), outline=colour, width=8)
    draw.text((420, 420), "CERTIFICATE OF DOMICILE", font=font(SERIF, 26), fill=(90, 20, 30))
    body = font(SERIF, 12)
    for line in range(18):
        words = " ".join(rng.choice(WORDS) for _ in range(9))
        draw.text((300, 800 + line * 90), words.capitalize() + ".", font=body, fill=(30, 30, 30))
    small = font(SANS, 10)
    draw.text((300, 2600), f"Certificate No. DOM/{rng.randint(10**5, 10**6)}/2025    Issued at Zenith City",
              font=small, fill=(30, 30, 30))
    # Coloured seal with text and a blue-ink signature.
    draw.ellipse((1700, 2750, 2150, 3200), fill=(200, 60, 60), outline=(150, 30, 30), width=12)
    draw.ellipse((1780, 2830, 2070, 3120), outline=(250, 220, 220), width=6)
    draw.text((1810, 2950), "OFFICIAL SEAL", font=font(SANS_BOLD, 8), fill=(255, 235, 235))
    points, px, py = [], 400.0, 3000.0
    for step in range(140):
        px += 5 + 3 * math.sin(step / 5)
        py += 16 * math.sin(step / 2.6 + rng.random())
        points.append((px, py))
    draw.line(points, fill=(30, 40, 150), width=7)
    return scanner_finish(page, seed)


def _images_to_pdf(images: list[Image.Image], dpi: int = DPI, quality: int = 92) -> bytes:
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", resolution=dpi, save_all=True, append_images=images[1:], quality=quality)
    return buffer.getvalue()


def synthetic_photo(seed: int, size=(2400, 1600)) -> Image.Image:
    rng = np.random.default_rng(seed)
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    sky = np.stack([100 + 80 * yy / height, 150 + 50 * yy / height, 230 - 60 * yy / height], -1)
    hills = (yy > height * (0.55 + 0.08 * np.sin(xx / 170) + 0.04 * np.sin(xx / 53)))
    sky[hills] = np.stack([60 + 30 * np.sin(xx / 40), 120 + 20 * np.cos(yy / 30), 60 + 0 * xx], -1)[hills]
    sky += rng.normal(0, 8, sky.shape)
    return Image.fromarray(np.clip(sky, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))


def digital_with_photo(seed: int) -> bytes:
    """Born-digital: vector Helvetica text and one large embedded JPEG photo."""
    rng = random.Random(seed)
    writer = PdfWriter()
    helvetica = writer._add_object(DictionaryObject({  # noqa: SLF001
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")}))
    photo = synthetic_photo(seed)
    jpeg = io.BytesIO()
    photo.save(jpeg, "JPEG", quality=95)
    image = StreamObject()
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(photo.width), NameObject("/Height"): NumberObject(photo.height),
                  NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                  NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Filter"): NameObject("/DCTDecode")})
    image._data = jpeg.getvalue()  # noqa: SLF001
    image_ref = writer._add_object(image)  # noqa: SLF001
    for page_index in range(2):
        page = writer.add_blank_page(595, 842)
        lines = []
        if page_index == 0:
            lines.append("q 480 0 0 320 57 470 cm /Im0 Do Q")
            lines.append("BT /F1 16 Tf 57 800 Td (Annual Statement of Account) Tj ET")
            y = 440
        else:
            y = 790
        while y > 60:
            words = " ".join(rng.choice(WORDS) for _ in range(12)).capitalize()
            lines.append(f"BT /F1 9 Tf 57 {y} Td ({words}.) Tj ET")
            y -= 14
        content = DecodedStreamObject()
        content.set_data("\n".join(lines).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
        resources = {NameObject("/Font"): DictionaryObject({NameObject("/F1"): helvetica})}
        if page_index == 0:
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): image_ref})
        page[NameObject("/Resources")] = DictionaryObject(resources)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def build() -> dict[str, bytes]:
    return {
        "passport_scan": _images_to_pdf([passport_page(1), passport_page(2)]),
        "marksheet_scan": _images_to_pdf([marksheet_page(s) for s in (10, 11, 12)], quality=90),
        "phone_photo": _images_to_pdf([phone_photo(20)], dpi=300, quality=90),
        "digital_with_photo": digital_with_photo(30),
        "certificate_scan": _images_to_pdf([certificate_page(40)]),
    }


if __name__ == "__main__":
    out = Path(__file__).parent / "corpus"
    out.mkdir(exist_ok=True)
    for name, data in build().items():
        (out / f"{name}.pdf").write_bytes(data)
        print(f"{name}: {len(data):,} bytes")
