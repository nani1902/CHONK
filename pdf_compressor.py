#!/usr/bin/env python3
"""Compress a PDF to a byte ceiling while preferring higher-fidelity profiles.

Compatibility launcher for the legacy command line. The implementation lives
in the ``chonk`` package under ``src/``; this file keeps
``python pdf_compressor.py INPUT --target-size SIZE`` working from a source
checkout and re-exports the names earlier callers imported from here.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import chonk  # noqa: F401
except ImportError:  # Running from a source checkout without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from chonk.adapters.cli import build_parser, main, parse_size, run  # noqa: E402
from chonk.backends.ghostscript import find_ghostscript, make_ghostscript_command  # noqa: E402
from chonk.inspection import pdf_page_count  # noqa: E402
from chonk.models import CompressionError, Profile  # noqa: E402
from chonk.search import Candidate, build_profiles, choose_profiles, dpi_steps  # noqa: E402
from chonk.sizes import SIZE_UNITS, human_size  # noqa: E402
from chonk.validation.structure import validate_pdf  # noqa: E402
from chonk.validation.visual import compare_visual_similarity, render_page  # noqa: E402

__all__ = [
    "SIZE_UNITS",
    "Candidate",
    "CompressionError",
    "Profile",
    "build_parser",
    "build_profiles",
    "choose_profiles",
    "compare_visual_similarity",
    "dpi_steps",
    "find_ghostscript",
    "human_size",
    "main",
    "make_ghostscript_command",
    "parse_size",
    "pdf_page_count",
    "render_page",
    "run",
    "validate_pdf",
]


if __name__ == "__main__":
    raise SystemExit(main())
