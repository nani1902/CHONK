"""Command line: ``chonk compress``, ``chonk inspect``, ``chonk doctor``, ``chonk mcp``.

Progress messages never include the input path, file name or anything read
from the document.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

from . import __version__, engine, netjail, ocr, posture
from .errors import ChonkError
from .sizes import human_size, parse_size
from .vault import write_private


def _size_arg(value: str) -> int:
    try:
        return parse_size(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chonk", description="Fit a PDF under a size limit, locally.")
    parser.add_argument("--version", action="version", version=f"chonk {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    compress = commands.add_parser("compress", help="compress a PDF to a size ceiling",
                                   formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    compress.add_argument("input", type=Path, help="source PDF")
    compress.add_argument("--target-size", "-t", required=True, type=_size_arg,
                          help="maximum output size, such as 200KB, 2MB or 1.5MiB")
    compress.add_argument("--output", "-o", type=Path, help="output PDF (default: <input>-compressed.pdf)")
    compress.add_argument("--min-dpi", type=int, default=72, help="lowest image resolution considered")
    compress.add_argument("--allow-grayscale", action="store_true",
                          help="convert colour images to grayscale if colour cannot fit")
    compress.add_argument("--strip-metadata", action="store_true", help="remove title/author/XMP metadata")
    compress.add_argument("--comparison-dpi", type=int, default=150, help="render resolution for SSIM")
    compress.add_argument("--max-attempts", type=int, default=48, help="maximum candidates built")
    compress.add_argument("--ocr-check", action="store_true",
                          help="also compare OCR character counts (needs tesseract or rapidocr)")
    compress.add_argument("--force", action="store_true", help="replace an existing output file")
    compress.add_argument("--json", action="store_true", help="print the result as JSON (numbers only)")
    compress.add_argument("--quiet", "-q", action="store_true", help="no progress messages")

    inspect = commands.add_parser("inspect", help="page count, size and kind of a PDF")
    inspect.add_argument("input", type=Path)

    doctor = commands.add_parser("doctor", help="check dependencies and the privacy lock")
    doctor.add_argument("--privacy", action="store_true", help="check the Claude Code privacy lock")
    doctor.add_argument("--vault", type=Path, help="vault folder (default: $CHONK_VAULT or ~/Private)")
    doctor.add_argument("--project", type=Path, help="project folder whose .claude settings to read")
    doctor.add_argument("--apply", action="store_true",
                        help="merge the lock into your user settings (~/.claude/settings.json), with a backup")
    doctor.add_argument("--snippet", action="store_true", help="print only the settings snippet")

    mcp = commands.add_parser("mcp", help="run the privacy-mode MCP server on stdio")
    mcp.add_argument("--vault", type=Path, help="vault folder (default: $CHONK_VAULT or ~/Private)")
    mcp.add_argument("--no-network", action="store_true",
                     help="re-run the server with networking removed by the OS (Linux only)")
    return parser


# ---------------------------------------------------------------------------


def cmd_compress(args: argparse.Namespace) -> int:
    source = args.input.expanduser()
    if not source.is_file():
        raise ChonkError("The input file does not exist.")
    output = (args.output.expanduser() if args.output
              else source.with_name(source.stem + "-compressed.pdf"))
    if output.suffix.lower() != ".pdf":
        raise ChonkError("The output filename must end in .pdf.")
    if output.resolve() == source.resolve():
        raise ChonkError("The output must be a different file from the input.")
    if output.exists() and not args.force:
        raise ChonkError("The output file already exists (pass --force to replace it).")

    say = None if args.quiet or args.json else (lambda message: print(message, file=sys.stderr))
    options = engine.Options(
        target_bytes=args.target_size, min_dpi=args.min_dpi, allow_grayscale=args.allow_grayscale,
        strip_metadata=args.strip_metadata, comparison_dpi=args.comparison_dpi,
        max_attempts=args.max_attempts, progress=say,
    )
    try:
        options.validate()
    except ValueError as exc:
        raise ChonkError(str(exc)) from exc
    data = engine.read_input(source, options.max_input_bytes)
    result = engine.compress_bytes(data, options)

    report: dict[str, object] = {
        "status": result.status, "source_bytes": result.source_bytes,
        "target_bytes": result.target_bytes, "pages": result.pages, "attempts": result.attempts,
    }
    if result.status == "infeasible" or result.output is None:
        report["smallest_bytes"] = result.smallest_bytes
        if args.json:
            print(json.dumps(report))
        else:
            print(f"No tested setting reached {human_size(result.target_bytes)}. The smallest was "
                  f"{human_size(result.smallest_bytes or 0)}. Try --allow-grayscale, a lower "
                  "--min-dpi, or a larger target. Nothing was written.", file=sys.stderr)
        return 3

    comparison = None
    if args.ocr_check and result.status == "fit":
        ocr_engine = ocr.find_engine()
        if ocr_engine is None:
            print("OCR check skipped: install tesseract or rapidocr-onnxruntime.", file=sys.stderr)
        else:
            worst = result.worst_page_index or 0
            pages = [worst] + [i for i in range(result.pages) if i != worst]
            comparison = ocr.compare(data, result.output, ocr_engine, pages[:10])

    write_private(output, result.output, overwrite=args.force)
    profile = result.profile or engine.LOSSLESS
    report.update({
        "bytes": result.output_bytes, "worst_page": result.worst_page_index,
        "worst_page_ssim": result.worst_page_ssim, "mean_ssim": result.mean_ssim,
        "max_dpi": profile.max_dpi, "jpeg_quality": profile.quality, "grayscale": profile.grayscale,
        "lost": list(result.lost),
    })
    if comparison is not None:
        report.update({"ocr_chars_compared": comparison.chars_compared,
                       "ocr_mismatches": comparison.mismatches})
    if args.json:
        print(json.dumps(report))
        return 0
    if result.status == "already_fits":
        print(f"Already under the limit ({human_size(result.source_bytes)}); copied unchanged.")
    else:
        print(f"Size: {human_size(result.output_bytes or 0)} (limit {human_size(result.target_bytes)})")
        print(f"Setting: {profile.label()}; {result.attempts} candidate(s) built")
        print(f"Worst page: {(result.worst_page_index or 0) + 1} of {result.pages}, "
              f"SSIM {result.worst_page_ssim:.4f} (mean {result.mean_ssim:.4f})")
    if comparison is not None:
        print(f"OCR: {comparison.chars_compared} characters compared, {comparison.mismatches} differ")
    if result.lost:
        print("Not preserved: " + ", ".join(result.lost))
    if not args.output:
        print(f"Saved as {output.name} next to the input.")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    info = engine.inspect(engine.read_input(args.input.expanduser()))
    print(json.dumps({"pages": info.pages, "bytes": info.bytes, "kind": info.kind, "signed": info.signed}))
    return 0


def _print_posture(report: posture.PostureReport) -> None:
    print(f"Vault: {posture.display_path(report.vault)}")
    print("Settings read: " + (", ".join(str(p) for p in report.sources) or "none found"))
    print(f"Privacy posture: {report.posture.upper()}")
    for code in report.missing:
        print(f"  ✗ {posture.EXPLANATIONS[code]}")
    for code in report.hardening:
        print(f"  • hardening: {posture.HARDENING[code]}")
    if report.posture == "walled":
        print("  ✓ The agent's file tools and sandboxed commands cannot read the vault.")


def cmd_doctor(args: argparse.Namespace) -> int:
    vault = (args.vault or posture.default_vault()).expanduser()
    snippet = posture.lock_snippet(vault)
    if args.snippet:
        print(json.dumps(snippet, indent=2))
        return 0

    if not args.privacy:
        print(f"chonk {__version__} on Python {sys.version.split()[0]} ({sys.platform})")
        engine_found = ocr.find_engine()
        print(f"OCR engine: {engine_found.name if engine_found else 'none (install tesseract or rapidocr-onnxruntime)'}")
        display = sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        print(f"Display for picker/review windows: {'yes' if display else 'no (headless: use list_vault)'}")
        print(f"OS network isolation for `chonk mcp --no-network`: {netjail.available() or 'unavailable'}")
        print("Run `chonk doctor --privacy` to check the Claude Code lock.")
        return 0

    report = posture.check(vault, args.project)
    _print_posture(report)
    if args.apply:
        return _apply_lock(snippet)
    if report.posture != "walled" or report.hardening:
        print("\nAdd this to ~/.claude/settings.json (or run `chonk doctor --privacy --apply`):\n")
        print(json.dumps(snippet, indent=2))
        if "platform_unsupported" in report.missing:
            print("\nOn native Windows these deny rules stop Claude's file tools but not scripts. "
                  "Run Claude Code inside WSL2 for OS-level enforcement.")
        print("\nThen register the server:  claude mcp add --scope user chonk -- "
              f"{sys.executable} -m chonk mcp")
    return 0 if report.posture == "walled" else 1


def _apply_lock(snippet: dict) -> int:
    settings_path = posture.claude_config_dir() / "settings.json"
    current: dict = {}
    if settings_path.is_file():
        try:
            current = json.loads(settings_path.read_text(encoding="utf-8"))
        except ValueError:
            print(f"\n{settings_path} is not valid JSON; fix it first. Nothing was changed.", file=sys.stderr)
            return 2
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = settings_path.with_name(f"settings.json.chonk-backup-{stamp}")
        shutil.copy2(settings_path, backup)
        print(f"\nBacked up {settings_path} to {backup.name}")
    merged = posture.merge_lock(current, snippet)
    write_private(settings_path, (json.dumps(merged, indent=2) + "\n").encode(), overwrite=True)
    print(f"Updated {settings_path}. Restart Claude Code, then run `chonk doctor --privacy` again.")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.no_network:
        argv = [sys.executable, "-m", "chonk", "mcp"]
        if args.vault:
            argv += ["--vault", str(args.vault)]
        try:
            wrapped = netjail.wrap(argv)
        except (NotImplementedError, RuntimeError) as exc:
            print(f"--no-network: {exc}", file=sys.stderr)
            return 2
        os.execvp(wrapped[0], wrapped)
    from .mcp_server import main as serve

    return serve(args.vault)


def main(argv: Sequence[str] | None = None) -> int:
    logging.getLogger().addHandler(logging.NullHandler())  # no library chatter on stderr
    args = build_parser().parse_args(argv)
    handlers = {"compress": cmd_compress, "inspect": cmd_inspect, "doctor": cmd_doctor, "mcp": cmd_mcp}
    try:
        return handlers[args.command](args)
    except ChonkError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # strerror only: the exception's str() includes the file path.
        print(f"Error: {exc.strerror or 'could not access a file'}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
