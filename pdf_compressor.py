#!/usr/bin/env python3
"""Compress a PDF to a byte ceiling while preferring higher-fidelity profiles."""

from __future__ import annotations

import argparse
import decimal
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

try:
    import pypdfium2 as pdfium
except ImportError:  # A friendly error is reported by main().
    pdfium = None  # type: ignore[assignment]

try:
    from pypdf import PdfReader
except ImportError:  # A friendly error is reported by main().
    PdfReader = None  # type: ignore[assignment]

try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:  # A friendly error is reported by main().
    Image = ImageChops = ImageStat = None  # type: ignore[assignment]


class CompressionError(Exception):
    """A user-facing compression error."""


@dataclass(frozen=True)
class Profile:
    dpi: int | None
    qfactor: float
    clarity_rank: float
    label: str


@dataclass(frozen=True)
class Candidate:
    profile_index: int
    profile: Profile
    path: Path
    size: int
    visual_similarity: float


SIZE_UNITS = {
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "kib": 1_024,
    "mib": 1_048_576,
    "gib": 1_073_741_824,
}


BIT_PREFIXES = {
    "": ("", "B"),
    "k": ("kilo", "KB"),
    "m": ("mega", "MB"),
    "g": ("giga", "GB"),
    "ki": ("kibi", "KiB"),
    "mi": ("mebi", "MiB"),
    "gi": ("gibi", "GiB"),
}


def _bit_unit_prefix(unit: str) -> str | None:
    """Return the prefix of a unit that denotes bits (``Mb``, ``Kib``, ``Mbit``)."""
    match = re.fullmatch(r"([kmg]i?)?bits?", unit, re.IGNORECASE)
    if match:
        return (match.group(1) or "").lower()
    # A lowercase "b" after an uppercase letter is the bit spelling (Kb, Mb, Gib).
    # All-lowercase spellings such as "mb" stay bytes, as portals commonly write.
    if unit.lower() in SIZE_UNITS and unit.endswith("b") and unit != unit.lower():
        return unit[:-1].lower()
    return None


def parse_size(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", value, re.ASCII)
    if not match:
        raise argparse.ArgumentTypeError(
            "use a size such as 500KB, 5MB, 5MiB, or 1200000B"
        )

    text, unit = match.groups()
    bit_prefix = _bit_unit_prefix(unit)
    if bit_prefix is not None:
        name, byte_unit = BIT_PREFIXES[bit_prefix]
        raise argparse.ArgumentTypeError(
            f"'{unit}' means {name}bits, not {name}bytes (1 byte = 8 bits); "
            f"use {byte_unit} for {name}bytes"
        )
    factor = SIZE_UNITS.get(unit.lower() or "b")
    if factor is None:
        raise argparse.ArgumentTypeError(
            "supported units are B, KB, MB, GB, KiB, MiB, and GiB"
        )

    # Exact decimal arithmetic: 4.1MB is 4,100,000 bytes, not a truncated float.
    with decimal.localcontext() as context:
        context.prec = len(text) + len(str(factor))
        context.traps[decimal.Inexact] = True
        size = decimal.Decimal(text) * factor
        if size != size.to_integral_value():
            raise argparse.ArgumentTypeError(
                f"{value.strip()} is {size.normalize():f} bytes; "
                "the size must be a whole number of bytes"
            )
    if size == 0:
        raise argparse.ArgumentTypeError("target size must be greater than zero")
    return int(size)


def human_size(size: int) -> str:
    for unit, factor in (("GiB", 1_073_741_824), ("MiB", 1_048_576), ("KiB", 1_024)):
        if size >= factor:
            return f"{size / factor:.2f} {unit} ({size:,} bytes)"
    return f"{size:,} bytes"


def dpi_steps(max_dpi: int, min_dpi: int, count: int = 9) -> list[int]:
    if max_dpi == min_dpi:
        return [max_dpi]
    values = {
        round(max_dpi * ((min_dpi / max_dpi) ** (step / (count - 1))))
        for step in range(count)
    }
    values.update((max_dpi, min_dpi))
    return sorted(values, reverse=True)


def build_profiles(max_dpi: int, min_dpi: int) -> list[Profile]:
    qfactors = (0.15, 0.25, 0.40, 0.55, 0.70, 0.85, 0.97)
    profiles: list[Profile] = [
        Profile(
            dpi=None,
            qfactor=0.15,
            clarity_rank=1.0,
            label="keep source resolution; highest image quality",
        )
    ]

    # Also try higher compression at source resolution before reducing detail.
    for qfactor in qfactors[1:]:
        quality = 1.0 - (qfactor - qfactors[0]) / (qfactors[-1] - qfactors[0])
        profiles.append(
            Profile(
                dpi=None,
                qfactor=qfactor,
                clarity_rank=0.65 + 0.35 * quality,
                label=f"keep source resolution; QFactor {qfactor:.2f}",
            )
        )

    for dpi in dpi_steps(max_dpi, min_dpi):
        dpi_score = math.sqrt(dpi / max_dpi)
        for qfactor in qfactors:
            quality = 1.0 - (qfactor - qfactors[0]) / (qfactors[-1] - qfactors[0])
            profiles.append(
                Profile(
                    dpi=dpi,
                    qfactor=qfactor,
                    clarity_rank=dpi_score * (0.65 + 0.35 * quality),
                    label=f"{dpi} dpi; QFactor {qfactor:.2f}",
                )
            )

    # Higher ranked profiles are attempted first. The score favors resolution
    # while still accounting for the image-compression setting.
    profiles.sort(key=lambda item: item.clarity_rank, reverse=True)

    # The source-resolution profile is the least destructive and always leads.
    source_profile = profiles.pop(next(i for i, p in enumerate(profiles) if p.dpi is None and p.qfactor == 0.15))
    profiles.insert(0, source_profile)
    return profiles


def find_ghostscript(explicit_path: str | None) -> str:
    if explicit_path:
        executable = shutil.which(explicit_path) or (
            explicit_path if Path(explicit_path).is_file() else None
        )
        if not executable:
            raise CompressionError(f"Ghostscript executable not found: {explicit_path}")
        return str(executable)

    names = ("gs", "gswin64c.exe", "gswin32c.exe")
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    raise CompressionError(
        "Ghostscript was not found. Install Ghostscript and rerun, or pass its path "
        "with --ghostscript."
    )


def pdf_page_count(path: Path) -> int:
    if PdfReader is None:
        raise CompressionError(
            "pypdf is required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )
    try:
        with path.open("rb") as stream:
            document = PdfReader(stream, strict=False)
            if document.is_encrypted:
                raise CompressionError(
                    "This PDF is password-protected. Unlock it before compressing."
                )
            page_count = len(document.pages)
            if page_count < 1:
                raise CompressionError("The input PDF has no pages.")
            return page_count
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Could not read input PDF: {exc}") from exc


def validate_pdf(path: Path, expected_pages: int) -> None:
    if PdfReader is None:
        raise CompressionError("pypdf is required to validate the compressed PDF.")
    try:
        with path.open("rb") as stream:
            document = PdfReader(stream, strict=False)
            if document.is_encrypted:
                raise CompressionError("Ghostscript produced an encrypted output PDF.")
            page_count = len(document.pages)
            if page_count != expected_pages:
                raise CompressionError(
                    "The compressed PDF page count changed "
                    f"({expected_pages} input pages, {page_count} output pages)."
                )
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Ghostscript produced an unreadable PDF: {exc}") from exc


def render_page(page, dpi: int):
    bitmap = page.render(
        scale=dpi / 72,
        draw_annots=True,
        fill_color=(255, 255, 255, 255),
        rev_byteorder=True,
    )
    try:
        return bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()


def compare_visual_similarity(
    source_path: Path,
    candidate_path: Path,
    dpi: int,
) -> float:
    """Return mean per-page pixel similarity, from 0 (different) to 1 (same)."""
    if pdfium is None or Image is None or ImageChops is None or ImageStat is None:
        raise CompressionError(
            "pypdfium2 and Pillow are required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )

    page_scores: list[float] = []
    try:
        with pdfium.PdfDocument(str(source_path)) as source, pdfium.PdfDocument(
            str(candidate_path)
        ) as document:
            if len(document) != len(source):
                raise CompressionError("Page count changed during visual comparison.")
            for page_number in range(len(document)):
                source_page = source[page_number]
                candidate_page = document[page_number]
                try:
                    original = render_page(source_page, dpi)
                    compressed = render_page(candidate_page, dpi)
                finally:
                    source_page.close()
                    candidate_page.close()

                width = max(original.width, compressed.width)
                height = max(original.height, compressed.height)
                original_canvas = Image.new("RGB", (width, height), "white")
                compressed_canvas = Image.new("RGB", (width, height), "white")
                original_canvas.paste(original, (0, 0))
                compressed_canvas.paste(compressed, (0, 0))

                channel_errors = ImageStat.Stat(
                    ImageChops.difference(original_canvas, compressed_canvas)
                ).rms
                pixel_error = sum(channel_errors) / (3 * 255)
                geometry_score = (
                    min(original.width, compressed.width) / width
                ) * (min(original.height, compressed.height) / height)
                page_scores.append(max(0.0, 1.0 - pixel_error) * geometry_score)
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Could not compare a compressed page render: {exc}") from exc

    if not page_scores:
        raise CompressionError("Could not compare an empty PDF.")
    return sum(page_scores) / len(page_scores)


def make_ghostscript_command(
    ghostscript: str,
    source: Path,
    output: Path,
    profile: Profile,
) -> list[str]:
    downsample = profile.dpi is not None
    args = [
        ghostscript,
        "-dSAFER",
        "-dBATCH",
        "-dNOPAUSE",
        "-dQUIET",
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=true",
        "-dPreserveAnnots=true",
        "-dPassThroughJPEGImages=true",
        "-dPassThroughJPXImages=true",
        f"-dDownsampleColorImages={str(downsample).lower()}",
        f"-dDownsampleGrayImages={str(downsample).lower()}",
        f"-dDownsampleMonoImages={str(downsample).lower()}",
        "-dColorImageDownsampleType=/Bicubic",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dMonoImageDownsampleType=/Subsample",
    ]

    if downsample:
        assert profile.dpi is not None
        args.extend(
            [
                f"-dColorImageResolution={profile.dpi}",
                f"-dGrayImageResolution={profile.dpi}",
                f"-dMonoImageResolution={profile.dpi}",
                "-dColorImageDownsampleThreshold=1.0",
                "-dGrayImageDownsampleThreshold=1.0",
                "-dMonoImageDownsampleThreshold=1.0",
            ]
        )

    qfactor = f"{profile.qfactor:.2f}"
    # Both ACS and regular dictionaries are set: Ghostscript may select one
    # based on its automatic image-filter decision.
    image_params = (
        "<< /LockDistillerParams true "
        f"/ColorImageDict << /QFactor {qfactor} >> "
        f"/GrayImageDict << /QFactor {qfactor} >> "
        f"/ColorACSImageDict << /QFactor {qfactor} /Blend 1 /ColorTransform 1 "
        "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
        f"/GrayACSImageDict << /QFactor {qfactor} /Blend 1 /ColorTransform 1 "
        "/HSamples [1 1 1 1] /VSamples [1 1 1 1] >> "
        ">> setdistillerparams"
    )
    args.extend(["-sOutputFile=" + str(output), "-c", image_params, "-f", str(source)])
    return args


def create_candidate(
    ghostscript: str,
    source: Path,
    workdir: Path,
    profile_index: int,
    profile: Profile,
    expected_pages: int,
    comparison_dpi: int,
    timeout: int,
) -> Candidate:
    candidate_path = workdir / f"candidate-{profile_index:03d}.pdf"
    command = make_ghostscript_command(ghostscript, source, candidate_path, profile)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompressionError(
            f"Ghostscript exceeded the {timeout}-second timeout while trying "
            f"{profile.label}."
        ) from exc
    except OSError as exc:
        raise CompressionError(f"Could not start Ghostscript: {exc}") from exc

    if result.returncode != 0 or not candidate_path.is_file():
        details = (result.stderr or result.stdout or "No diagnostic was returned.").strip()
        details = details[-2_000:]
        raise CompressionError(
            f"Ghostscript could not compress the PDF using {profile.label}.\n{details}"
        )

    validate_pdf(candidate_path, expected_pages)
    size = candidate_path.stat().st_size
    if size <= 0:
        raise CompressionError("Ghostscript created an empty output PDF.")
    similarity = compare_visual_similarity(source, candidate_path, comparison_dpi)
    return Candidate(profile_index, profile, candidate_path, size, similarity)


def choose_profiles(
    profiles: Sequence[Profile],
    target_size: int,
    max_attempts: int,
    run_candidate,
) -> tuple[Candidate | None, Candidate, int]:
    """Binary-search the ordered clarity ladder, then inspect nearby profiles."""
    results: dict[int, Candidate] = {}

    def attempt(index: int) -> Candidate | None:
        if index in results:
            return results[index]
        if len(results) >= max_attempts:
            return None
        candidate = run_candidate(index, profiles[index])
        results[index] = candidate
        print(
            f"  {candidate.size:,} bytes — {candidate.profile.label}; "
            f"render similarity {candidate.visual_similarity:.4%}",
            file=sys.stderr,
        )
        return candidate

    highest_fidelity = attempt(0)
    assert highest_fidelity is not None
    if highest_fidelity.size <= target_size:
        return highest_fidelity, highest_fidelity, len(results)

    lowest_fidelity = attempt(len(profiles) - 1)
    if lowest_fidelity is None:
        return None, highest_fidelity, len(results)
    if lowest_fidelity.size > target_size:
        return None, lowest_fidelity, len(results)

    best_fit_index = len(profiles) - 1
    best_nonfit_index = 0
    while best_fit_index - best_nonfit_index > 1 and len(results) < max_attempts:
        midpoint = (best_fit_index + best_nonfit_index) // 2
        candidate = attempt(midpoint)
        if candidate is None:
            break
        if candidate.size <= target_size:
            best_fit_index = midpoint
        else:
            best_nonfit_index = midpoint

    # Check nearby profiles to catch small non-monotonic size changes between
    # Ghostscript configurations.
    neighbors = [
        best_fit_index - 2,
        best_fit_index - 1,
        best_fit_index + 1,
        best_fit_index + 2,
    ]
    for index in neighbors:
        if 0 <= index < len(profiles) and len(results) < max_attempts:
            attempt(index)

    fitting = [candidate for candidate in results.values() if candidate.size <= target_size]
    best = max(
        fitting,
        key=lambda candidate: (candidate.visual_similarity, -candidate.profile_index),
    )
    smallest = min(results.values(), key=lambda candidate: candidate.size)
    return best, smallest, len(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compress a PDF to a size ceiling and choose the tested result with "
            "the closest rendered match to the source."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="source PDF")
    parser.add_argument(
        "--target-size",
        required=True,
        type=parse_size,
        help="maximum output size, such as 500KB, 5MB, or 5MiB",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output PDF (default: <input>-compressed.pdf)",
    )
    parser.add_argument(
        "--min-dpi",
        type=int,
        default=72,
        help="lowest image resolution considered; lower values can reduce clarity",
    )
    parser.add_argument(
        "--max-dpi",
        type=int,
        default=600,
        help="highest image resolution in the search profiles",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=16,
        help="maximum Ghostscript runs used to search the profile ladder",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="seconds allowed for each Ghostscript run",
    )
    parser.add_argument(
        "--comparison-dpi",
        type=int,
        default=150,
        help="rendering resolution for comparing candidate pages to the source",
    )
    parser.add_argument(
        "--ghostscript",
        help="Ghostscript executable path (auto-detected by default)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing output file after a successful compression",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.min_dpi < 1 or args.max_dpi < 1:
        raise CompressionError("DPI values must be positive integers.")
    if args.min_dpi > args.max_dpi:
        raise CompressionError("--min-dpi cannot be greater than --max-dpi.")
    if args.max_attempts < 2:
        raise CompressionError("--max-attempts must be at least 2.")
    if args.timeout < 1:
        raise CompressionError("--timeout must be a positive number of seconds.")
    if args.comparison_dpi < 36 or args.comparison_dpi > 600:
        raise CompressionError("--comparison-dpi must be between 36 and 600.")

    source = args.input.expanduser().resolve(strict=True)
    if not source.is_file():
        raise CompressionError(f"Input is not a file: {source}")

    output = args.output.expanduser() if args.output else source.with_name(
        source.stem + "-compressed.pdf"
    )
    output = output.resolve()
    if output.suffix.lower() != ".pdf":
        raise CompressionError("Output filename must end in .pdf.")
    if output == source:
        raise CompressionError("Output path must be different from the input path.")
    if output.exists() and not args.force:
        raise CompressionError(
            f"Output already exists: {output} (pass --force to replace it)."
        )

    expected_pages = pdf_page_count(source)
    ghostscript = find_ghostscript(args.ghostscript)
    profiles = build_profiles(args.max_dpi, args.min_dpi)
    output.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"Input: {source} ({human_size(source.stat().st_size)}, {expected_pages} pages)",
        file=sys.stderr,
    )
    print(f"Target: at most {human_size(args.target_size)}", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="pdf-compress-") as temporary_dir:
        workdir = Path(temporary_dir)

        def run_candidate(index: int, profile: Profile) -> Candidate:
            print(
                f"Trying profile {index + 1}/{len(profiles)}: {profile.label}",
                file=sys.stderr,
            )
            return create_candidate(
                ghostscript,
                source,
                workdir,
                index,
                profile,
                expected_pages,
                args.comparison_dpi,
                args.timeout,
            )

        best, smallest, attempt_count = choose_profiles(
            profiles,
            args.target_size,
            args.max_attempts,
            run_candidate,
        )
        if best is None:
            raise CompressionError(
                f"No tested profile reached the size ceiling. The smallest result was "
                f"{human_size(smallest.size)} after {attempt_count} attempts; "
                "try a lower --min-dpi or a larger --target-size. No output was written."
            )

        if best.size > args.target_size:
            raise CompressionError("Internal error: selected output exceeds the size ceiling.")

        output.parent.mkdir(parents=True, exist_ok=True)
        fd, staged_name = tempfile.mkstemp(
            prefix=f".{output.stem}.", suffix=".tmp.pdf", dir=output.parent
        )
        os.close(fd)
        staged = Path(staged_name)
        try:
            shutil.copyfile(best.path, staged)
            validate_pdf(staged, expected_pages)
            if staged.stat().st_size > args.target_size:
                raise CompressionError(
                    "The staged output is larger than the requested size ceiling."
                )
            if output.exists() and not args.force:
                raise CompressionError(
                    f"Output appeared while compressing: {output} "
                    "(pass --force to replace it)."
                )
            os.replace(staged, output)
        finally:
            staged.unlink(missing_ok=True)

    print(
        f"Saved {output}\n"
        f"Size: {human_size(best.size)} (ceiling {human_size(args.target_size)})\n"
        f"Profile: {best.profile.label}; {attempt_count} attempt(s)\n"
        f"Render similarity: {best.visual_similarity:.4%} "
        f"at {args.comparison_dpi} dpi",
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (CompressionError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
