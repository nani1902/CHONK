"""Compression backends. A backend rewrites a PDF for one profile; it does not
choose profiles, validate output, or decide product policy."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from chonk.models import Profile


class CompressionBackend(Protocol):
    def compress(
        self, source: Path, output: Path, profile: Profile, *, timeout: int
    ) -> None:
        """Write ``output`` from ``source`` using ``profile`` or raise
        :class:`chonk.models.CompressionError`."""
        ...
