"""Byte-size parsing and formatting."""

from __future__ import annotations

import re
from decimal import ROUND_FLOOR, Decimal

SIZE_UNITS = {
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "kib": 1_024,
    "mib": 1_048_576,
    "gib": 1_073_741_824,
}


def parse_size(value: str) -> int:
    """Return the exact byte count for a size such as ``2MB`` or ``4.5MiB``.

    Decimal arithmetic avoids binary floating-point error (``4.1MB`` is
    4,100,000 bytes, not 4,099,999). Fractional bytes round down so the ceiling
    never exceeds what was requested. Raises ``ValueError`` on invalid input.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", value)
    if not match:
        raise ValueError("use a size such as 500KB, 5MB, 5MiB, or 1200000B")

    unit = match.group(2).lower() or "b"
    if unit not in SIZE_UNITS:
        raise ValueError("supported units are B, KB, MB, GB, KiB, MiB, and GiB")
    amount = Decimal(match.group(1)) * SIZE_UNITS[unit]
    size = int(amount.to_integral_value(rounding=ROUND_FLOOR))
    if size <= 0:
        raise ValueError("target size must be greater than zero")
    return size


def human_size(size: int) -> str:
    for unit, factor in (("GiB", 1_073_741_824), ("MiB", 1_048_576), ("KiB", 1_024)):
        if size >= factor:
            return f"{size / factor:.2f} {unit} ({size:,} bytes)"
    return f"{size:,} bytes"
