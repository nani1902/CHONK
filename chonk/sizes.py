"""Parse and format byte sizes."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

SIZE_UNITS = {
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "kib": 1_024,
    "mib": 1_048_576,
    "gib": 1_073_741_824,
}

_SIZE_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*")


def parse_size(value: str | int) -> int:
    """Return a byte count for ``200KB``, ``4.5 MiB``, ``1200000`` and so on.

    Decimal units (KB, MB, GB) are powers of 1000; binary units (KiB, MiB, GiB)
    are powers of 1024. Fractions of a byte round down. Raises ``ValueError``.
    """
    if isinstance(value, bool):
        raise ValueError("use a size such as 500KB, 5MB, 5MiB, or 1200000B")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("target size must be greater than zero")
        return value

    match = _SIZE_RE.fullmatch(value)
    if not match:
        raise ValueError("use a size such as 500KB, 5MB, 5MiB, or 1200000B")
    unit = match.group(2).lower() or "b"
    if unit not in SIZE_UNITS:
        raise ValueError("supported units are B, KB, MB, GB, KiB, MiB, and GiB")
    try:
        # Decimal avoids float error: 4.1MB is exactly 4,100,000 bytes.
        size = int(Decimal(match.group(1)) * SIZE_UNITS[unit])
    except InvalidOperation as exc:  # pragma: no cover - the regex prevents this
        raise ValueError("could not read that size") from exc
    if size <= 0:
        raise ValueError("target size must be greater than zero")
    return size


def human_size(size: int) -> str:
    for unit, factor in (("GiB", 1_073_741_824), ("MiB", 1_048_576), ("KiB", 1_024)):
        if size >= factor:
            return f"{size / factor:.2f} {unit} ({size:,} bytes)"
    return f"{size:,} bytes"
