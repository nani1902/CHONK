"""Legacy human-readable command line: ``pdf_compressor.py INPUT --target-size N``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from chonk.adapters.text import describe_progress, describe_success, describe_target_not_met
from chonk.backends.ghostscript import GhostscriptBackend
from chonk.engine import compress_pdf
from chonk.models import (
    CompressionError,
    CompressionRequest,
    OutputExistsError,
    ProgressEvent,
    ResultStatus,
)
from chonk.sizes import parse_size as parse_size_bytes

_DEFAULTS = CompressionRequest(source=Path(), output=Path(), target_bytes=1)


def parse_size(value: str) -> int:
    """``argparse`` type wrapper around :func:`chonk.sizes.parse_size`."""
    try:
        return parse_size_bytes(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


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
        default=_DEFAULTS.min_dpi,
        help="lowest image resolution considered; lower values can reduce clarity",
    )
    parser.add_argument(
        "--max-dpi",
        type=int,
        default=_DEFAULTS.max_dpi,
        help="highest image resolution in the search profiles",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=_DEFAULTS.max_attempts,
        help="maximum Ghostscript runs used to search the profile ladder",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULTS.timeout_seconds,
        help="seconds allowed for each Ghostscript run",
    )
    parser.add_argument(
        "--comparison-dpi",
        type=int,
        default=_DEFAULTS.comparison_dpi,
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


def default_output(source: Path) -> Path:
    # Non-strict resolution follows symlinks like the engine does, without
    # failing here for a missing input; the engine reports that.
    resolved = source.expanduser().resolve()
    return resolved.with_name(resolved.stem + "-compressed.pdf")


def request_from_args(args: argparse.Namespace) -> CompressionRequest:
    return CompressionRequest(
        source=args.input,
        output=args.output if args.output else default_output(args.input),
        target_bytes=args.target_size,
        overwrite=args.force,
        min_dpi=args.min_dpi,
        max_dpi=args.max_dpi,
        max_attempts=args.max_attempts,
        timeout_seconds=args.timeout,
        comparison_dpi=args.comparison_dpi,
    )


def _print_progress(event: ProgressEvent) -> None:
    print(describe_progress(event), end="", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    """Run one compression from parsed arguments.

    Returns 0 on success. Raises :class:`CompressionError` or :class:`OSError`
    for failures, including a missed size target, as the legacy CLI did.
    """
    request = request_from_args(args)
    backend = None
    if args.ghostscript:
        backend = GhostscriptBackend.discover(args.ghostscript)
    try:
        result = compress_pdf(request, backend=backend, on_progress=_print_progress)
    except OutputExistsError as exc:
        verb = "appeared while compressing" if exc.appeared_during_run else "already exists"
        raise CompressionError(
            f"Output {verb}: {exc.path} (pass --force to replace it)."
        ) from exc
    if result.status is ResultStatus.TARGET_NOT_MET:
        raise CompressionError(describe_target_not_met(result, cli_hint=True))
    print(describe_success(result))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (CompressionError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
