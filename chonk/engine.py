"""Fit a PDF under a byte ceiling by re-encoding its images, in memory.

How it works:

1. A lossless pass: compress content streams and merge identical objects.
2. Collect the raster images that are safe to re-encode (8-bit gray, RGB,
   palette or ICC-based; no stencil masks, colour-key masks or custom
   ``/Decode`` arrays; nothing CMYK, Lab, DeviceN or Separation).
3. For each resolution cap, from "keep resolution" downwards, binary-search the
   highest JPEG quality whose output fits the ceiling. Measure the worst-page
   SSIM of each level's best fit and keep the best one.

Only the images change. Pages, text, fonts, vectors, annotations, form fields
and the document structure are carried over by pypdf. Candidates live in
memory; nothing is written to a temporary file.

Resolution is estimated as image pixels divided by the page size. An image
normally covers at most its page, so this is a lower bound on the image's real
resolution: a cap of 150 dpi never leaves an image below 150 dpi as displayed,
except when an image is drawn larger than the page and clipped.
"""

from __future__ import annotations

import inspect as inspect_module
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    NumberObject,
    StreamObject,
)

from . import quality
from .errors import (
    ChonkError,
    EmptyPdfError,
    EncryptedPdfError,
    NotPdfError,
    OutputInvalidError,
    TooLargeError,
    UnreadablePdfError,
)

try:  # pypdf >= 5.2
    from pypdf.generic._image_xobject import _xobj_to_image
except ImportError:  # pragma: no cover - older pypdf
    from pypdf.filters import _xobj_to_image  # type: ignore[no-redef]

log = logging.getLogger("chonk.engine")

Progress = Callable[[str], None]

# Resolution caps tried after "keep source resolution", highest first.
DPI_STEPS = (400, 300, 250, 200, 170, 150, 125, 100, 85, 72, 60, 50)
# JPEG qualities, highest first.
QUALITIES = (92, 85, 78, 70, 60, 50, 40, 30)
# Images smaller than this are left alone: re-encoding them saves little.
MIN_IMAGE_STREAM_BYTES = 4_096
MIN_IMAGE_SIDE = 64
# Decoded pixels kept in memory between candidates; larger sets are re-decoded.
DECODE_CACHE_BYTES = 768 * 1024 * 1024
DEFAULT_MAX_INPUT_BYTES = 500 * 1024 * 1024

_SAFE_COLORSPACES = {"/DeviceGray", "/DeviceRGB", "/CalGray", "/CalRGB"}
# Keys carried over from the original image dictionary to its replacement.
_KEPT_IMAGE_KEYS = ("/SMask", "/Intent", "/Interpolate", "/OC", "/StructParent")

Status = Literal["fit", "already_fits", "infeasible"]
Kind = Literal["scanned_images", "scanned_with_text_layer", "mixed", "text_or_vector"]


@dataclass(frozen=True)
class Profile:
    """One point in the search. ``quality=None`` leaves every image untouched."""

    max_dpi: int | None
    quality: int | None
    grayscale: bool = False

    @property
    def lossless(self) -> bool:
        return self.quality is None

    def label(self) -> str:
        if self.lossless:
            return "lossless (images untouched)"
        resolution = "source resolution" if self.max_dpi is None else f"≤{self.max_dpi} dpi"
        colour = ", grayscale" if self.grayscale else ""
        return f"{resolution}, JPEG quality {self.quality}{colour}"


LOSSLESS = Profile(None, None)


@dataclass
class Options:
    target_bytes: int
    min_dpi: int = 72
    allow_grayscale: bool = False
    strip_metadata: bool = False
    comparison_dpi: int = 150
    max_attempts: int = 48
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    progress: Progress | None = None

    def validate(self) -> None:
        if self.target_bytes <= 0:
            raise ValueError("target size must be greater than zero")
        if not 1 <= self.min_dpi <= 1200:
            raise ValueError("min_dpi must be between 1 and 1200")
        if not 36 <= self.comparison_dpi <= 600:
            raise ValueError("comparison_dpi must be between 36 and 600")
        if self.max_attempts < 2:
            raise ValueError("max_attempts must be at least 2")


@dataclass
class DocumentInfo:
    pages: int
    bytes: int
    kind: Kind
    signed: bool


@dataclass
class Result:
    status: Status
    source_bytes: int
    target_bytes: int
    pages: int
    output: bytes | None = None
    profile: Profile | None = None
    worst_page_ssim: float | None = None
    worst_page_index: int | None = None
    mean_ssim: float | None = None
    smallest_bytes: int | None = None
    attempts: int = 0
    lost: tuple[str, ...] = ()
    images_total: int = 0
    images_reencoded: int = 0

    @property
    def output_bytes(self) -> int | None:
        return None if self.output is None else len(self.output)


# --------------------------------------------------------------------------
# Reading and inspecting


def open_pdf(data: bytes) -> PdfReader:
    """Open ``data`` with pypdf and map every failure to a fixed error code."""
    if not data.lstrip()[:5].startswith(b"%PDF-") and b"%PDF-" not in data[:1024]:
        raise NotPdfError()
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        encrypted = reader.is_encrypted
    except Exception as exc:  # library text is logged locally, never returned
        log.warning("pypdf could not open the input: %s", type(exc).__name__)
        raise UnreadablePdfError() from exc
    if encrypted:
        raise EncryptedPdfError()
    try:
        count = len(reader.pages)
    except Exception as exc:
        log.warning("pypdf could not count pages: %s", type(exc).__name__)
        raise UnreadablePdfError() from exc
    if count < 1:
        raise EmptyPdfError()
    return reader


def read_input(path: Path, max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES) -> bytes:
    size = path.stat().st_size
    if size > max_input_bytes:
        raise TooLargeError()
    return path.read_bytes()


def is_signed(reader: PdfReader) -> bool:
    try:
        root = reader.trailer["/Root"].get_object()
        form = root.get("/AcroForm")
        if form is None:
            return False
        form = form.get_object()
        if int(form.get("/SigFlags", 0)) & 1:
            return True
        for field_ref in form.get("/Fields", []) or []:
            field_obj = field_ref.get_object()
            if field_obj.get("/FT") == "/Sig" and field_obj.get("/V") is not None:
                return True
    except Exception:  # a broken form dictionary is not a signature
        return False
    return False


def classify(data: bytes) -> Kind:
    """Label the document from a fixed list, using pdfium locally.

    Text is counted, never extracted into a result.
    """
    document = pdfium.PdfDocument(data)
    try:
        pages_covered = 0
        any_image = False
        characters = 0
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                area = max(width * height, 1.0)
                coverage = 0.0
                for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3):
                    any_image = True
                    bounds = getattr(obj, "get_bounds", None) or obj.get_pos  # v5 / v4
                    left, bottom, right, top = bounds()
                    coverage = max(coverage, (right - left) * (top - bottom) / area)
                if coverage >= 0.8:
                    pages_covered += 1
                textpage = page.get_textpage()
                try:
                    characters += textpage.count_chars()
                finally:
                    textpage.close()
            finally:
                page.close()
        if pages_covered == len(document):
            return "scanned_images" if characters == 0 else "scanned_with_text_layer"
        return "mixed" if any_image else "text_or_vector"
    finally:
        document.close()


def inspect(data: bytes) -> DocumentInfo:
    reader = open_pdf(data)
    try:
        kind = classify(data)
    except Exception as exc:
        log.warning("pdfium could not classify the input: %s", type(exc).__name__)
        raise UnreadablePdfError() from exc
    return DocumentInfo(len(reader.pages), len(data), kind, is_signed(reader))


# --------------------------------------------------------------------------
# Image discovery


ImagePath = tuple  # (page index, xobject name, nested xobject name, ...)


def _inherited(page: DictionaryObject, key: str):
    node = page
    for _ in range(64):  # guard against a /Parent cycle
        if key in node:
            return node[key]
        parent = node.get("/Parent")
        if parent is None:
            return None
        node = parent.get_object()
    return None


def _walk_xobjects(resources, prefix: tuple, seen_forms: set[int]) -> Iterator[tuple[tuple, IndirectObject]]:
    if resources is None:
        return
    resources = resources.get_object()
    xobjects = resources.get("/XObject") if isinstance(resources, DictionaryObject) else None
    if xobjects is None:
        return
    xobjects = xobjects.get_object()
    if not isinstance(xobjects, DictionaryObject):
        return
    for name in sorted(xobjects.keys()):
        reference = xobjects.raw_get(name)
        if not isinstance(reference, IndirectObject):
            continue
        obj = reference.get_object()
        if not isinstance(obj, DictionaryObject):
            continue
        subtype = obj.get("/Subtype")
        if subtype == "/Image":
            yield prefix + (str(name),), reference
        elif subtype == "/Form" and reference.idnum not in seen_forms:
            seen_forms.add(reference.idnum)
            yield from _walk_xobjects(obj.get("/Resources"), prefix + (str(name),), seen_forms)


def iter_images(writer: PdfWriter) -> Iterator[tuple[tuple, IndirectObject, float, float]]:
    """Yield (path, reference, page width in inches, page height in inches)."""
    for index, page in enumerate(writer.pages):
        box = page.mediabox
        width_in = max(float(box.width), 1.0) / 72
        height_in = max(float(box.height), 1.0) / 72
        for path, reference in _walk_xobjects(_inherited(page, "/Resources"), (index,), set()):
            yield path, reference, width_in, height_in


def _skip_reason(obj: DictionaryObject) -> str | None:
    """Why an image must be left untouched, or None if it is safe to re-encode."""
    if obj.get("/ImageMask"):
        return "stencil_mask"
    if isinstance(obj.get("/Mask"), ArrayObject):
        return "colour_key_mask"  # exact colours matter; JPEG would break the mask
    if obj.get("/Decode") is not None:
        return "decode_array"
    if int(obj.get("/BitsPerComponent", 8) or 8) < 8:
        return "low_bit_depth"  # bilevel scans compress better losslessly
    colorspace = obj.get("/ColorSpace")
    if colorspace is not None:
        colorspace = colorspace.get_object()
        family = colorspace[0] if isinstance(colorspace, ArrayObject) and colorspace else colorspace
        family = str(family)
        if family == "/ICCBased":
            try:
                components = int(colorspace[1].get_object().get("/N", 0))
            except Exception:
                return "colorspace"
            if components not in (1, 3):
                return "colorspace"
        elif family == "/Indexed":
            base = colorspace[1].get_object() if len(colorspace) > 1 else None
            base_family = str(base[0] if isinstance(base, ArrayObject) and base else base)
            if base_family not in _SAFE_COLORSPACES and base_family != "/ICCBased":
                return "colorspace"
        elif family not in _SAFE_COLORSPACES:
            return "colorspace"  # CMYK, Lab, DeviceN, Separation, Pattern
    try:
        width = int(obj.get("/Width", 0))
        height = int(obj.get("/Height", 0))
    except Exception:
        return "geometry"
    if width < MIN_IMAGE_SIDE and height < MIN_IMAGE_SIDE:
        return "small"
    return None


def _stream_length(obj) -> int:
    data = getattr(obj, "_data", None)
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    try:
        return int(obj.get("/Length", 0))
    except Exception:
        return 0


@dataclass
class ImageRecord:
    record_id: int
    width: int
    height: int
    est_dpi: float
    original_bytes: int
    is_colour: bool
    decoded: Image.Image | None = None


class ImageSet:
    """The document's re-encodable images and a bounded decode cache."""

    def __init__(self, base: bytes):
        self.base = base
        self.records: list[ImageRecord] = []
        self.by_path: dict[tuple, int] = {}
        self.total_images = 0
        self._cached_bytes = 0
        self._scan()

    def _scan(self) -> None:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(self.base), strict=False))
        first_path_for: dict[int, int] = {}
        for path, reference, width_in, height_in in iter_images(writer):
            if reference.idnum in first_path_for:
                record_id = first_path_for[reference.idnum]
                if record_id >= 0:
                    self.by_path[path] = record_id
                    record = self.records[record_id]
                    record.est_dpi = max(
                        record.est_dpi,
                        max(record.width / width_in, record.height / height_in),
                    )
                continue
            self.total_images += 1
            obj = reference.get_object()
            reason = _skip_reason(obj)
            original_bytes = _stream_length(obj)
            if reason is None and original_bytes < MIN_IMAGE_STREAM_BYTES:
                reason = "small"
            if reason is not None:
                first_path_for[reference.idnum] = -1
                continue
            image = self._decode(obj)
            if image is None:
                first_path_for[reference.idnum] = -1
                continue
            record = ImageRecord(
                record_id=len(self.records),
                width=image.width,
                height=image.height,
                est_dpi=max(image.width / width_in, image.height / height_in),
                original_bytes=original_bytes,
                is_colour=image.mode not in ("L", "LA", "I", "I;16", "F", "1"),
            )
            self._remember(record, image)
            self.records.append(record)
            first_path_for[reference.idnum] = record.record_id
            self.by_path[path] = record.record_id

    @staticmethod
    def _decode(obj) -> Image.Image | None:
        try:
            _, _, image = _xobj_to_image(obj)
        except Exception as exc:
            log.info("skipping an image pypdf could not decode: %s", type(exc).__name__)
            return None
        if not isinstance(image, Image.Image):
            return None
        if image.mode in ("RGBA", "RGBX", "P", "PA", "CMYK", "YCbCr"):
            # Alpha stays in the untouched /SMask; palette images become RGB.
            image = image.convert("RGB")
        elif image.mode in ("LA", "I", "I;16", "F"):
            image = image.convert("L")
        if image.mode not in ("L", "RGB"):
            return None
        return image

    def _remember(self, record: ImageRecord, image: Image.Image) -> None:
        size = image.width * image.height * len(image.getbands())
        if self._cached_bytes + size <= DECODE_CACHE_BYTES:
            record.decoded = image
            self._cached_bytes += size

    def pixels(self, record: ImageRecord, reference: IndirectObject) -> Image.Image | None:
        if record.decoded is not None:
            return record.decoded
        return self._decode(reference.get_object())

    def scale_for(self, record: ImageRecord, max_dpi: int | None) -> float:
        if max_dpi is None or record.est_dpi <= max_dpi:
            return 1.0
        return max_dpi / record.est_dpi


# --------------------------------------------------------------------------
# Building candidates


def _lossless_pass(source: bytes, strip_metadata: bool) -> bytes:
    reader = PdfReader(io.BytesIO(source), strict=False)
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        try:
            page.compress_content_streams(level=9)
        except Exception as exc:
            log.info("left one content stream as it was: %s", type(exc).__name__)
    try:
        writer.compress_identical_objects(**_dedupe_arguments())
    except Exception as exc:
        log.info("skipped identical-object merging: %s", type(exc).__name__)
    if strip_metadata:
        _strip_metadata(writer)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _dedupe_arguments() -> dict[str, bool]:
    """pypdf renamed these keywords; use whichever this version accepts."""
    parameters = inspect_module.signature(PdfWriter.compress_identical_objects).parameters
    duplicates = "remove_duplicates" if "remove_duplicates" in parameters else "remove_identicals"
    unreferenced = "remove_unreferenced" if "remove_unreferenced" in parameters else "remove_orphans"
    return {duplicates: True, unreferenced: True}


def _strip_metadata(writer: PdfWriter) -> None:
    writer.metadata = None
    root = writer._root_object  # noqa: SLF001 - pypdf has no public setter for this
    if "/Metadata" in root:
        del root["/Metadata"]


def _jpeg(image: Image.Image, scale: float, quality_value: int, grayscale: bool) -> bytes:
    if grayscale and image.mode != "L":
        image = image.convert("L")
    if scale < 1.0:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
    buffer = io.BytesIO()
    save_args = {"quality": quality_value, "optimize": True}
    if image.mode == "RGB":
        # Full-resolution chroma at high quality keeps coloured text and stamps
        # sharp; 4:2:0 below that saves bytes where they matter more.
        save_args["subsampling"] = 0 if quality_value >= 85 else 2
    image.save(buffer, "JPEG", **save_args)
    return buffer.getvalue()


def _jpeg_image_stream(original: DictionaryObject, data: bytes, width: int, height: int, gray: bool) -> StreamObject:
    stream = StreamObject()
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Image")
    stream[NameObject("/Width")] = NumberObject(width)
    stream[NameObject("/Height")] = NumberObject(height)
    stream[NameObject("/ColorSpace")] = NameObject("/DeviceGray" if gray else "/DeviceRGB")
    stream[NameObject("/BitsPerComponent")] = NumberObject(8)
    stream[NameObject("/Filter")] = NameObject("/DCTDecode")
    for key in _KEPT_IMAGE_KEYS:
        if key in original:
            stream[NameObject(key)] = original.raw_get(key)
    stream._data = data  # noqa: SLF001 - already-encoded bytes, written as is
    return stream


@dataclass(eq=False)
class _Built:
    profile: Profile
    size: int
    data: bytes | None  # dropped for candidates the search no longer needs
    reencoded: int
    converted_colour: bool
    scores: quality.PageScores | None = None


class _Search:
    def __init__(self, source: bytes, base: bytes, images: ImageSet, options: Options):
        self.source = source
        self.base = base
        self.images = images
        self.options = options
        self.attempts = 0
        self.cache: dict[Profile, _Built] = {}
        self.reference = quality.Reference(source, options.comparison_dpi)

    def say(self, message: str) -> None:
        if self.options.progress:
            self.options.progress(message)

    def budget_left(self) -> bool:
        return self.attempts < self.options.max_attempts

    def build(self, profile: Profile) -> _Built:
        cached = self.cache.get(profile)
        if cached is not None:
            return cached
        self.attempts += 1
        if profile.lossless:
            built = _Built(profile, len(self.base), self.base, 0, False)
        else:
            built = self._build_lossy(profile)
        self.cache[profile] = built
        self.say(f"  tried {profile.label()}: {built.size:,} bytes")
        return built

    def materialize(self, built: _Built) -> bytes:
        """The candidate's bytes, rebuilding them if they were dropped."""
        if built.data is None:
            rebuilt = self._build_lossy(built.profile)
            if rebuilt.size != built.size:  # pragma: no cover - encoders are deterministic
                raise OutputInvalidError()
            built.data = rebuilt.data
        return built.data

    def prune(self, *keep: _Built | None) -> None:
        """Drop candidate bytes the search no longer needs, to bound memory."""
        kept = {id(item) for item in keep if item is not None}
        for built in self.cache.values():
            if id(built) not in kept and not built.profile.lossless:
                built.data = None

    def _build_lossy(self, profile: Profile) -> _Built:
        assert profile.quality is not None
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(self.base), strict=False))
        done: set[int] = set()
        reencoded = 0
        converted_colour = False
        for path, reference, _, _ in iter_images(writer):
            record_id = self.images.by_path.get(path)
            if record_id is None or reference.idnum in done:
                continue
            done.add(reference.idnum)
            record = self.images.records[record_id]
            scale = self.images.scale_for(record, profile.max_dpi)
            pixels = self.images.pixels(record, reference)
            if pixels is None:
                continue
            data = _jpeg(pixels, scale, profile.quality, profile.grayscale)
            # Keep the original whenever re-encoding would not make it smaller.
            if len(data) >= record.original_bytes:
                continue
            gray = profile.grayscale or not record.is_colour
            width = max(1, round(record.width * scale)) if scale < 1.0 else record.width
            height = max(1, round(record.height * scale)) if scale < 1.0 else record.height
            original = reference.get_object()
            writer._replace_object(reference, _jpeg_image_stream(original, data, width, height, gray))  # noqa: SLF001
            reencoded += 1
            converted_colour = converted_colour or (profile.grayscale and record.is_colour)
        buffer = io.BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
        return _Built(profile, len(data), data, reencoded, converted_colour)

    def score(self, built: _Built) -> quality.PageScores:
        if built.scores is None:
            built.scores = self.reference.compare(self.materialize(built))
            self.say(f"  worst-page SSIM {built.scores.worst:.4f} for {built.profile.label()}")
        return built.scores

    def fits(self, built: _Built) -> bool:
        return built.size <= self.options.target_bytes

    def dpi_levels(self) -> list[int | None]:
        """Resolution caps that actually change at least one image, deduplicated."""
        levels: list[int | None] = []
        seen: set[tuple[float, ...]] = set()
        for max_dpi in (None, *[d for d in DPI_STEPS if d >= self.options.min_dpi]):
            signature = tuple(round(self.images.scale_for(r, max_dpi), 4) for r in self.images.records)
            if signature in seen:
                continue
            seen.add(signature)
            levels.append(max_dpi)
        return levels

    def best_quality_at(self, max_dpi: int | None, grayscale: bool) -> _Built | None:
        """Highest quality at this resolution cap that fits, by binary search."""
        profiles = [Profile(max_dpi, q, grayscale) for q in QUALITIES]
        top = self.build(profiles[0])
        if self.fits(top):
            return top
        if not self.budget_left():
            return None
        bottom = self.build(profiles[-1])
        if not self.fits(bottom):
            return None
        fit_index, miss_index = len(profiles) - 1, 0
        while fit_index - miss_index > 1 and self.budget_left():
            middle = (fit_index + miss_index) // 2
            if self.fits(self.build(profiles[middle])):
                fit_index = middle
            else:
                miss_index = middle
        return self.build(profiles[fit_index])

    def run_ladder(self, grayscale: bool) -> tuple[_Built | None, _Built]:
        levels = self.dpi_levels()
        floor = self.build(Profile(levels[-1], QUALITIES[-1], grayscale))
        smallest = floor
        if not self.fits(floor):
            return None, smallest
        best: _Built | None = None
        declines = 0
        for max_dpi in levels:
            if not self.budget_left():
                break
            built = self.best_quality_at(max_dpi, grayscale)
            if built is None:
                continue
            if built.size < smallest.size:
                smallest = built
            if best is None or self.score(built).worst > self.score(best).worst:
                best = built
                declines = 0
            else:
                declines += 1
                if declines >= 2:
                    break  # quality has peaked; lower resolutions only get worse
            if built.profile.quality == QUALITIES[0]:
                break  # top quality fits here; lower resolution cannot beat it
            self.prune(best, smallest, floor)
        if best is None:
            best = floor
            self.score(best)
        return best, smallest


# --------------------------------------------------------------------------
# Entry points


def compress_bytes(source: bytes, options: Options) -> Result:
    """Return the best-fitting result for ``source``. Never writes files."""
    options.validate()
    if len(source) > options.max_input_bytes:
        raise TooLargeError()
    reader = open_pdf(source)
    pages = len(reader.pages)
    signed = is_signed(reader)
    target = options.target_bytes
    say = options.progress or (lambda _message: None)
    say(f"Input: {pages} page(s), {len(source):,} bytes; ceiling {target:,} bytes")

    if len(source) <= target and not options.strip_metadata:
        return Result("already_fits", len(source), target, pages, output=source,
                      profile=LOSSLESS, worst_page_ssim=1.0, worst_page_index=0,
                      mean_ssim=1.0, attempts=0)

    try:
        base = _lossless_pass(source, options.strip_metadata)
    except ChonkError:
        raise
    except Exception as exc:
        log.warning("lossless pass failed: %s", type(exc).__name__)
        raise UnreadablePdfError() from exc
    if len(base) >= len(source) and not options.strip_metadata:
        base = source
    try:
        images = ImageSet(base)
    except Exception as exc:
        log.warning("image scan failed: %s", type(exc).__name__)
        raise UnreadablePdfError() from exc
    say(f"Re-encodable images: {len(images.records)} of {images.total_images}")

    search = _Search(source, base, images, options)
    try:
        chosen, smallest = _choose(search, options)
    except ChonkError:
        raise
    except Exception as exc:
        log.exception("search failed")
        raise UnreadablePdfError() from exc

    lost: list[str] = []
    if signed:
        lost.append("digital_signature")
    if options.strip_metadata:
        lost.append("metadata")

    if chosen is None:
        return Result("infeasible", len(source), target, pages, smallest_bytes=smallest.size,
                      attempts=search.attempts, lost=(), images_total=images.total_images)

    output = search.materialize(chosen)
    _validate_output(output, pages, target)
    scores = search.score(chosen)
    if chosen.converted_colour:
        lost.append("color")
    return Result(
        "fit", len(source), target, pages,
        output=output,
        profile=chosen.profile,
        worst_page_ssim=scores.worst,
        worst_page_index=scores.worst_index,
        mean_ssim=scores.mean,
        smallest_bytes=smallest.size,
        attempts=search.attempts,
        lost=tuple(lost),
        images_total=images.total_images,
        images_reencoded=chosen.reencoded,
    )


def _choose(search: _Search, options: Options) -> tuple[_Built | None, _Built]:
    lossless = search.build(LOSSLESS)
    if search.fits(lossless):
        search.score(lossless)
        return lossless, lossless
    if not search.images.records:
        return None, lossless
    best, smallest = search.run_ladder(grayscale=False)
    if best is None and options.allow_grayscale:
        best, gray_smallest = search.run_ladder(grayscale=True)
        if gray_smallest.size < smallest.size:
            smallest = gray_smallest
    if lossless.size < smallest.size:
        smallest = lossless
    return best, smallest


def _validate_output(data: bytes, expected_pages: int, target: int) -> None:
    if len(data) > target:
        raise OutputInvalidError()
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted or len(reader.pages) != expected_pages:
            raise OutputInvalidError()
        if quality.page_count(data) != expected_pages:
            raise OutputInvalidError()
    except OutputInvalidError:
        raise
    except Exception as exc:
        log.error("output failed validation: %s", type(exc).__name__)
        raise OutputInvalidError() from exc


def compress_file(path: Path, options: Options) -> Result:
    return compress_bytes(read_input(path, options.max_input_bytes), options)
