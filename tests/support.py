"""Shared test helpers."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


def copy_fixture(corpus_dir: Path, name: str, destination: Path) -> Path:
    target = destination / f"{name}.pdf"
    shutil.copyfile(corpus_dir / f"{name}.pdf", target)
    return target


@dataclass(frozen=True)
class FileState:
    sha256: str
    size: int
    mtime_ns: int
    mode: int

    @classmethod
    def of(cls, path: Path) -> "FileState":
        stat = path.stat()
        return cls(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_mode,
        )
