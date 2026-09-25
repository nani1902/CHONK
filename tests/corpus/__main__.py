"""Write the synthetic corpus and a provenance manifest to a directory.

Usage (from the repository root):

    PYTHONPATH=tests python -m corpus OUTPUT_DIR

The manifest records each fixture's intent, expected baseline behavior, and
SHA-256, together with the library versions that produced the bytes. Byte
identity is guaranteed only for the same library versions: JPEG encoding and
PDF encryption output may differ across Pillow or pypdf releases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import zlib
from pathlib import Path

import cryptography
import PIL
import pypdf

from .catalog import FIXTURES, PROVENANCE

MANIFEST_SCHEMA = "chonk-test-corpus/1"


def manifest(directory: Path) -> dict:
    fixtures = []
    for spec in FIXTURES:
        data = (directory / spec.filename).read_bytes()
        fixtures.append(
            {
                "name": spec.name,
                "file": spec.filename,
                "family": spec.family,
                "intent": spec.intent,
                "features": list(spec.features),
                "has_text_layer": spec.has_text_layer,
                "baseline": spec.baseline,
                "baseline_accepts": spec.baseline_accepts,
                "preflight": spec.preflight,
                "generator": f"tests/corpus/catalog.py:{spec.build.__name__}",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "schema": MANIFEST_SCHEMA,
        "provenance": PROVENANCE,
        "contains_real_data": False,
        "generated_with": {
            "python": platform.python_version(),
            "pypdf": pypdf.__version__,
            "pillow": PIL.__version__,
            "cryptography": cryptography.__version__,
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
        },
        "fixtures": fixtures,
    }


def write_corpus(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for spec in FIXTURES:
        (directory / spec.filename).write_bytes(spec.build())
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest(directory), indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    path = write_corpus(args.output_dir)
    print(f"Wrote {len(FIXTURES)} fixtures and {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
