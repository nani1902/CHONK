"""Print the lowest versions the requirement files allow, pinned with ``==``.

Reads a requirements file, follows ``-r`` includes, and turns every
``name>=X`` into ``name==X``. A requirement without a ``>=`` floor is an
error, so a new unbounded dependency cannot slip past the lowest-versions
CI job.

    python .github/scripts/lowest_requirements.py requirements-dev.txt
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FLOOR = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*>=\s*([^\s,;]+)$")


def floors(path: Path, seen: set[Path]) -> list[str]:
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    pins = []
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ")):
            include = line.split(None, 1)[1]
            pins += floors(path.parent / include, seen)
            continue
        match = FLOOR.match(line)
        if not match:
            raise SystemExit(
                f"{path.name}:{number}: expected 'name>=version', got {raw.strip()!r}"
            )
        name, extras, version = match.groups()
        pins.append(f"{name}{extras or ''}=={version}")
    return pins


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    print("\n".join(floors(Path(argv[0]), set())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
