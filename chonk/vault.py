"""The vault folder, opaque document handles, and saving results.

Handles are random and live only in the server's memory. They are never
derived from a path, filename or document content.
"""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
from pathlib import Path

from .posture import default_vault

HANDLE_PREFIX = "doc_"
OUTBOX = "outbox"
STATE_DIR = ".chonk"


class UnknownHandleError(KeyError):
    pass


class Vault:
    def __init__(self, root: Path | None = None):
        self.root = (root or default_vault()).expanduser().resolve()

    @property
    def outbox(self) -> Path:
        return self.root / OUTBOX

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    def ensure(self) -> None:
        for directory in (self.root, self.outbox, self.state_dir):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                directory.chmod(0o700)
            except OSError:
                pass

    def contains(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def documents(self) -> list[Path]:
        """PDFs in the vault, newest first. The outbox and state dir are skipped."""
        if not self.root.is_dir():
            return []
        found = []
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root)
            if relative.parts and relative.parts[0] in (OUTBOX, STATE_DIR):
                continue
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_file() and path.suffix.lower() == ".pdf":
                found.append(path)
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def outbox_path(self, source: Path) -> Path:
        """A new, unused name in the outbox. Only the user sees this name."""
        stem = source.stem[:80] or "document"
        candidate = self.outbox / f"{stem}-chonk.pdf"
        counter = 2
        while candidate.exists():
            candidate = self.outbox / f"{stem}-chonk-{counter}.pdf"
            counter += 1
        return candidate


class Handles:
    def __init__(self) -> None:
        self._by_handle: dict[str, Path] = {}
        self._by_path: dict[Path, str] = {}
        self._lock = threading.Lock()

    def handle_for(self, path: Path) -> str:
        resolved = path.resolve()
        with self._lock:
            existing = self._by_path.get(resolved)
            if existing:
                return existing
            handle = HANDLE_PREFIX + secrets.token_hex(4)
            while handle in self._by_handle:
                handle = HANDLE_PREFIX + secrets.token_hex(4)
            self._by_handle[handle] = resolved
            self._by_path[resolved] = handle
            return handle

    def path_for(self, handle: str) -> Path:
        with self._lock:
            try:
                return self._by_handle[handle]
            except KeyError:
                raise UnknownHandleError(handle) from None


def write_private(path: Path, data: bytes, overwrite: bool = False) -> None:
    """Write atomically with owner-only permissions; never replaces by default."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staged_name = tempfile.mkstemp(prefix=".chonk-", suffix=".tmp", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            staged.chmod(0o600)
        except OSError:
            pass
        if overwrite:
            os.replace(staged, path)
        else:
            # link() fails if the target exists, so a file that appeared since
            # the name was chosen is never clobbered.
            try:
                os.link(staged, path)
            except (AttributeError, NotImplementedError, PermissionError):
                if path.exists():
                    raise FileExistsError(path)
                os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
