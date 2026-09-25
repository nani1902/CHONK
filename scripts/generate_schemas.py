"""Regenerate the checked-in JSON Schemas from the contract definitions.

Usage: python scripts/generate_schemas.py
A test fails if the checked-in files differ from the generated ones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chonk.contract import json_schemas  # noqa: E402
from chonk.models import SCHEMA_VERSION  # noqa: E402


def render(schema: dict) -> str:
    return json.dumps(schema, indent=2) + "\n"


def main() -> int:
    directory = ROOT / "schemas" / SCHEMA_VERSION
    directory.mkdir(parents=True, exist_ok=True)
    for name, schema in json_schemas().items():
        (directory / f"{name}.schema.json").write_text(render(schema), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
