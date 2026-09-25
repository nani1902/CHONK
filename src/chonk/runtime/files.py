"""Session-scoped file grants, stable source snapshots, and safe output publication.

A person (or the host integration acting for them) grants specific input files
and output directories to a local client session. Callers then refer to those
files only by opaque grant IDs. This module enforces that:

* grant IDs work only for the session that received them and only until they
  are revoked, expire, or the session closes;
* a granted source is bound to the exact file identity and bytes seen at grant
  time; symlinked, hard-linked, replaced, or modified sources are rejected;
* processing works on a private snapshot copied from a stable read;
* output names are single ``.pdf`` components inside a granted directory; and
* publication writes the checked bytes to a staged file and exposes them with
  one atomic operation. The default never replaces an existing name, even one
  created concurrently. Replacement requires both a grant that permits it and
  an explicit request, and never replaces a granted source.

Error messages never contain paths, filenames, or hashes, so they are safe to
relay to an agent. Hashes and paths on the returned records stay local.

Threats from a process running as the same OS user with write access to the
same directories are outside this boundary (see ``docs/product/ARCHITECTURE.md``
section 6). The checks here narrow races with such processes but cannot
exclude them.

Only POSIX systems providing ``O_NOFOLLOW`` and ``dir_fd`` operations are
supported; elsewhere every operation fails with ``PLATFORM_UNSUPPORTED``.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Sequence

_CHUNK = 1024 * 1024
_MAX_NAME_BYTES = 255
_WINDOWS_UNSAFE_CHARACTERS = frozenset('<>:"|?*\\')
_WINDOWS_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


class FileReason(str, Enum):
    """Reason codes raised by this module.

    The first four match the codes listed in the architecture. The last two are
    proposed additions for CHONK-004 to adopt or rename in the shared model.
    """

    ACCESS_DENIED = "ACCESS_DENIED"
    SOURCE_CHANGED = "SOURCE_CHANGED"
    OUTPUT_CONFLICT = "OUTPUT_CONFLICT"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    ARTIFACT_MISMATCH = "ARTIFACT_MISMATCH"
    PLATFORM_UNSUPPORTED = "PLATFORM_UNSUPPORTED"


class FileAccessError(Exception):
    """A refused file operation. ``str(error)`` never contains local paths."""

    def __init__(self, reason: FileReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    @property
    def reason_code(self) -> str:
        return self.reason.value


class Overwrite(str, Enum):
    NO_CLOBBER = "no_clobber"
    REPLACE = "replace"


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int

    @classmethod
    def of(cls, info: os.stat_result) -> FileIdentity:
        return cls(info.st_dev, info.st_ino)


@dataclass(frozen=True)
class ClientSession:
    session_id: str


@dataclass(frozen=True)
class FileGrant:
    grant_id: str
    session_id: str
    path: Path = field(repr=False)
    identity: FileIdentity = field(repr=False)
    size: int
    sha256: str = field(repr=False)
    expires_at: float | None


@dataclass(frozen=True)
class DestinationGrant:
    grant_id: str
    session_id: str
    directory: Path = field(repr=False)
    identity: FileIdentity = field(repr=False)
    allow_replace: bool
    expires_at: float | None


@dataclass(frozen=True)
class SourceSnapshot:
    """A private copy of a granted source, taken from one stable read."""

    file_id: str
    session_id: str
    path: Path = field(repr=False)
    size: int
    sha256: str = field(repr=False)


@dataclass(frozen=True)
class PublishedOutput:
    destination_id: str
    path: Path = field(repr=False)
    size: int
    sha256: str = field(repr=False)
    replaced_existing: bool


@dataclass
class _GrantRecord:
    grant: FileGrant | DestinationGrant
    revoked: bool = False


def _denied(message: str = "The grant is not valid for this session.") -> FileAccessError:
    return FileAccessError(FileReason.ACCESS_DENIED, message)


def _platform_supported() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and all(
            function in os.supports_dir_fd
            for function in (os.open, os.stat, os.unlink, os.rename, os.link)
        )
    )


def _require_platform() -> None:
    if not _platform_supported():
        raise FileAccessError(
            FileReason.PLATFORM_UNSUPPORTED,
            "Safe file access is not supported on this platform.",
        )


def _canonical(path: Path | str) -> Path:
    """Resolve symlinks in parent components only; the final component is kept."""
    absolute = Path(path).expanduser().absolute()
    if absolute.name in ("", ".", ".."):
        return Path(os.path.realpath(absolute))
    return Path(os.path.realpath(absolute.parent)) / absolute.name


def _open_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0)


def _open_directory(path: Path | str, name: str = "") -> int:
    """Open a directory without following a symlink in its final component."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise _denied(f"The {name or 'directory'} cannot be opened safely.") from exc


def _open_regular_file(path: Path | str, *, dir_fd: int | None = None) -> tuple[int, os.stat_result]:
    """Open a regular, singly linked file without following a final symlink.

    ``O_NONBLOCK`` prevents a FIFO planted at the path from blocking the open.
    """
    try:
        fd = os.open(path, _open_flags(), dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise _denied("Symbolic links are not accepted as source files.") from exc
        raise _denied("The source file cannot be opened.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise _denied("Only regular files can be granted.")
        if info.st_nlink != 1:
            raise _denied("Files with multiple hard links are not accepted as sources.")
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def _write_all(fd: int, data: bytes | memoryview) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _stable_read(
    fd: int,
    *,
    sink: int | None = None,
    max_bytes: int | None = None,
) -> tuple[str, int]:
    """Hash (and optionally copy) a file, failing if it changes while read."""
    before = os.fstat(fd)
    if max_bytes is not None and before.st_size > max_bytes:
        raise FileAccessError(
            FileReason.RESOURCE_LIMIT_EXCEEDED, "The source file exceeds the size limit."
        )
    digest = hashlib.sha256()
    total = 0
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, _CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > before.st_size:
            break
        digest.update(chunk)
        if sink is not None:
            _write_all(sink, chunk)
    after = os.fstat(fd)
    if total != before.st_size or (
        (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise FileAccessError(
            FileReason.SOURCE_CHANGED, "The source file changed while it was being read."
        )
    return digest.hexdigest(), total


def validate_output_name(filename: str) -> str:
    """Return ``filename`` if it is a safe single-component PDF name.

    Rejects separators, traversal, hidden names, control characters, and
    characters or forms that Windows cannot represent safely.
    """
    if not isinstance(filename, str) or not filename:
        raise _denied("The output name is not allowed.")
    if (
        filename in (".", "..")
        or filename.startswith(".")
        or "/" in filename
        or (os.altsep and os.altsep in filename)
        or any(ch in _WINDOWS_UNSAFE_CHARACTERS for ch in filename)
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in filename)
        or filename != filename.strip()
        or filename.endswith(".")
        or not filename.lower().endswith(".pdf")
        or len(filename) <= len(".pdf")
        or filename.split(".")[0].upper() in _WINDOWS_RESERVED_STEMS
    ):
        raise _denied("The output name is not allowed.")
    try:
        encoded = os.fsencode(filename)
    except UnicodeError as exc:
        raise _denied("The output name is not allowed.") from exc
    if len(encoded) > _MAX_NAME_BYTES:
        raise _denied("The output name is too long.")
    return filename


class _NoReplaceUnsupported(Exception):
    pass


def _rename_noreplace(src_name: str, dst_name: str, dir_fd: int) -> None:
    """Atomically rename within ``dir_fd``, failing if the target name exists."""
    libc = ctypes.CDLL(None, use_errno=True)
    src = os.fsencode(src_name)
    dst = os.fsencode(dst_name)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        function = libc.renameat2
        flag = 1  # RENAME_NOREPLACE
    elif sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        function = libc.renameatx_np
        flag = 0x4  # RENAME_EXCL
    else:
        raise _NoReplaceUnsupported
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(dir_fd, src, dir_fd, dst, flag) == 0:
        return
    code = ctypes.get_errno()
    if code in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
        raise _NoReplaceUnsupported
    raise OSError(code, os.strerror(code))


def _link_noreplace(src_name: str, dst_name: str, dir_fd: int) -> None:
    """Expose the staged inode under ``dst_name``; ``link`` never replaces."""
    try:
        os.link(src_name, dst_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.ENOSYS):
            raise _NoReplaceUnsupported from exc
        raise
    os.unlink(src_name, dir_fd=dir_fd)


def _publish_noreplace(src_name: str, dst_name: str, dir_fd: int) -> None:
    for strategy in (_rename_noreplace, _link_noreplace):
        try:
            strategy(src_name, dst_name, dir_fd)
            return
        except _NoReplaceUnsupported:
            continue
    raise FileAccessError(
        FileReason.PLATFORM_UNSUPPORTED,
        "The destination file system cannot publish without risking an overwrite.",
    )


class GrantRegistry:
    """In-memory store of session-scoped grants. Safe to share between threads.

    ``grant_file``, ``grant_destination``, and session creation are host/human
    functions. They must not be exposed as agent tools.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: set[str] = set()
        self._grants: dict[str, _GrantRecord] = {}

    # Sessions -----------------------------------------------------------------

    def open_session(self) -> ClientSession:
        session = ClientSession(f"session_{secrets.token_urlsafe(16)}")
        with self._lock:
            self._sessions.add(session.session_id)
        return session

    def close_session(self, session: ClientSession) -> None:
        """End a session and revoke every grant it holds."""
        with self._lock:
            self._sessions.discard(session.session_id)
            for record in self._grants.values():
                if record.grant.session_id == session.session_id:
                    record.revoked = True

    # Grants -------------------------------------------------------------------

    def grant_file(
        self, session: ClientSession, path: Path | str, *, ttl_seconds: float | None = None
    ) -> FileGrant:
        """Grant read access to one regular file as it exists now."""
        _require_platform()
        self._require_session(session)
        canonical = _canonical(path)
        fd, info = _open_regular_file(canonical)
        try:
            sha256, size = _stable_read(fd)
        finally:
            os.close(fd)
        grant = FileGrant(
            grant_id=f"file_{secrets.token_urlsafe(16)}",
            session_id=session.session_id,
            path=canonical,
            identity=FileIdentity.of(info),
            size=size,
            sha256=sha256,
            expires_at=self._expiry(ttl_seconds),
        )
        self._store(session, grant)
        return grant

    def grant_destination(
        self,
        session: ClientSession,
        directory: Path | str,
        *,
        allow_replace: bool = False,
        ttl_seconds: float | None = None,
    ) -> DestinationGrant:
        """Grant permission to create PDF files directly inside ``directory``."""
        _require_platform()
        self._require_session(session)
        canonical = _canonical(directory)
        fd = _open_directory(canonical, "destination directory")
        try:
            info = os.fstat(fd)
        finally:
            os.close(fd)
        grant = DestinationGrant(
            grant_id=f"dest_{secrets.token_urlsafe(16)}",
            session_id=session.session_id,
            directory=canonical,
            identity=FileIdentity.of(info),
            allow_replace=allow_replace,
            expires_at=self._expiry(ttl_seconds),
        )
        self._store(session, grant)
        return grant

    def revoke(self, session: ClientSession, grant_id: str) -> None:
        """Revoke a grant held by ``session``. Revoking twice is harmless."""
        with self._lock:
            record = self._grants.get(grant_id)
            if record is None or record.grant.session_id != session.session_id:
                raise _denied()
            record.revoked = True

    def file_grant(self, session: ClientSession, grant_id: str) -> FileGrant:
        grant = self._lookup(session, grant_id)
        if not isinstance(grant, FileGrant):
            raise _denied()
        return grant

    def destination_grant(self, session: ClientSession, grant_id: str) -> DestinationGrant:
        grant = self._lookup(session, grant_id)
        if not isinstance(grant, DestinationGrant):
            raise _denied()
        return grant

    # Sources ------------------------------------------------------------------

    def snapshot_source(
        self,
        session: ClientSession,
        file_id: str,
        workspace: Path | str,
        *,
        max_bytes: int | None = None,
    ) -> SourceSnapshot:
        """Copy the granted bytes into ``workspace`` as a private (0600) file.

        Fails with ``SOURCE_CHANGED`` if the file at the granted path is not the
        granted file, or its bytes differ from those seen at grant time.
        """
        _require_platform()
        grant = self.file_grant(session, file_id)
        workspace_fd = _open_directory(workspace, "workspace")
        name = f"source-{secrets.token_hex(12)}.pdf"
        try:
            source_fd = self._open_granted_source(grant)
            try:
                snapshot_fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=workspace_fd,
                )
                try:
                    sha256, size = _stable_read(source_fd, sink=snapshot_fd, max_bytes=max_bytes)
                    os.fsync(snapshot_fd)
                except BaseException:
                    os.close(snapshot_fd)
                    _unlink_quietly(name, workspace_fd)
                    raise
                os.close(snapshot_fd)
            finally:
                os.close(source_fd)
            if (sha256, size) != (grant.sha256, grant.size):
                _unlink_quietly(name, workspace_fd)
                raise FileAccessError(
                    FileReason.SOURCE_CHANGED,
                    "The source file changed after access was granted; grant it again.",
                )
        finally:
            os.close(workspace_fd)
        return SourceSnapshot(
            file_id=grant.grant_id,
            session_id=session.session_id,
            path=Path(workspace) / name,
            size=size,
            sha256=sha256,
        )

    def verify_source_unchanged(self, session: ClientSession, snapshot: SourceSnapshot) -> None:
        """Confirm the grant is still valid and the original still has the snapshot bytes."""
        _require_platform()
        grant = self.file_grant(session, snapshot.file_id)
        fd = self._open_granted_source(grant)
        try:
            sha256, size = _stable_read(fd)
        finally:
            os.close(fd)
        if (sha256, size) != (snapshot.sha256, snapshot.size):
            raise FileAccessError(
                FileReason.SOURCE_CHANGED, "The source file changed during processing."
            )

    # Publication --------------------------------------------------------------

    def publish(
        self,
        session: ClientSession,
        destination_id: str,
        filename: str,
        artifact: Path | str,
        *,
        expected_sha256: str,
        expected_size: int,
        max_bytes: int | None = None,
        overwrite: Overwrite = Overwrite.NO_CLOBBER,
        sources: Sequence[SourceSnapshot] = (),
    ) -> PublishedOutput:
        """Publish exactly the checked artifact bytes as ``filename``.

        ``expected_sha256``/``expected_size`` identify the validated artifact;
        different bytes are refused. ``max_bytes`` re-checks the size ceiling.
        Every snapshot in ``sources`` must still match its original. The staged
        file is exposed with an atomic no-replace operation by default, so an
        output created concurrently by anyone else is never overwritten.
        """
        _require_platform()
        overwrite = Overwrite(overwrite)
        grant = self.destination_grant(session, destination_id)
        validate_output_name(filename)
        if overwrite is Overwrite.REPLACE and not grant.allow_replace:
            raise _denied("This destination does not permit replacing existing files.")
        for snapshot in sources:
            self.verify_source_unchanged(session, snapshot)

        dir_fd = _open_directory(grant.directory, "destination directory")
        staged_name = f".chonk-{secrets.token_hex(12)}.tmp"
        staged_fd: int | None = None
        try:
            if FileIdentity.of(os.fstat(dir_fd)) != grant.identity:
                raise _denied("The destination directory was moved or replaced; grant it again.")
            existing = _lstat_or_none(filename, dir_fd)
            if existing is not None:
                if overwrite is Overwrite.NO_CLOBBER:
                    raise _conflict()
                self._check_replaceable(existing)

            staged_fd = os.open(
                staged_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
            self._stage_artifact(artifact, staged_fd, expected_sha256, expected_size, max_bytes)
            staged_identity = FileIdentity.of(os.fstat(staged_fd))

            replaced = False
            if overwrite is Overwrite.NO_CLOBBER:
                try:
                    _publish_noreplace(staged_name, filename, dir_fd)
                except FileExistsError as exc:
                    raise _conflict() from exc
            else:
                # Re-check immediately before the atomic replacement.
                existing = _lstat_or_none(filename, dir_fd)
                if existing is not None:
                    self._check_replaceable(existing)
                    replaced = True
                os.rename(staged_name, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)

            published = _lstat_or_none(filename, dir_fd)
            if published is None or FileIdentity.of(published) != staged_identity:
                raise FileAccessError(
                    FileReason.ARTIFACT_MISMATCH,
                    "The published file was replaced by another process after publication.",
                )
            return PublishedOutput(
                destination_id=grant.grant_id,
                path=grant.directory / filename,
                size=expected_size,
                sha256=expected_sha256,
                replaced_existing=replaced,
            )
        finally:
            if staged_fd is not None:
                os.close(staged_fd)
            _unlink_quietly(staged_name, dir_fd)
            os.close(dir_fd)

    # Internals ----------------------------------------------------------------

    def _expiry(self, ttl_seconds: float | None) -> float | None:
        if ttl_seconds is None:
            return None
        if not ttl_seconds > 0:
            raise ValueError("ttl_seconds must be positive")
        return self._clock() + ttl_seconds

    def _require_session(self, session: ClientSession) -> None:
        with self._lock:
            if session.session_id not in self._sessions:
                raise _denied("The session is closed or unknown.")

    def _store(self, session: ClientSession, grant: FileGrant | DestinationGrant) -> None:
        with self._lock:
            if session.session_id not in self._sessions:
                raise _denied("The session is closed or unknown.")
            self._grants[grant.grant_id] = _GrantRecord(grant)

    def _lookup(self, session: ClientSession, grant_id: str) -> FileGrant | DestinationGrant:
        with self._lock:
            record = self._grants.get(grant_id) if isinstance(grant_id, str) else None
            # Unknown IDs and other sessions' IDs are indistinguishable.
            if record is None or record.grant.session_id != session.session_id:
                raise _denied()
            if session.session_id not in self._sessions or record.revoked:
                raise _denied("The grant was revoked; ask the user to grant access again.")
            expires_at = record.grant.expires_at
            if expires_at is not None and self._clock() >= expires_at:
                record.revoked = True
                raise _denied("The grant expired; ask the user to grant access again.")
            return record.grant

    def _source_identities(self) -> Iterable[FileIdentity]:
        with self._lock:
            return [
                record.grant.identity
                for record in self._grants.values()
                if isinstance(record.grant, FileGrant)
            ]

    def _open_granted_source(self, grant: FileGrant) -> int:
        try:
            fd, info = _open_regular_file(grant.path)
        except FileAccessError as exc:
            raise FileAccessError(
                FileReason.SOURCE_CHANGED,
                "The granted source file is missing or was replaced; grant it again.",
            ) from exc
        if FileIdentity.of(info) != grant.identity:
            os.close(fd)
            raise FileAccessError(
                FileReason.SOURCE_CHANGED,
                "The granted source file was replaced; grant it again.",
            )
        return fd

    def _check_replaceable(self, existing: os.stat_result) -> None:
        if not stat.S_ISREG(existing.st_mode):
            raise _conflict()
        if FileIdentity.of(existing) in self._source_identities():
            raise _denied("A granted source file cannot be replaced by an output.")

    @staticmethod
    def _stage_artifact(
        artifact: Path | str,
        staged_fd: int,
        expected_sha256: str,
        expected_size: int,
        max_bytes: int | None,
    ) -> None:
        if max_bytes is not None and expected_size > max_bytes:
            raise FileAccessError(
                FileReason.ARTIFACT_MISMATCH, "The artifact exceeds the requested size ceiling."
            )
        try:
            artifact_fd = os.open(artifact, _open_flags())
        except OSError as exc:
            raise FileAccessError(
                FileReason.ARTIFACT_MISMATCH, "The checked artifact is not available."
            ) from exc
        try:
            if not stat.S_ISREG(os.fstat(artifact_fd).st_mode):
                raise FileAccessError(
                    FileReason.ARTIFACT_MISMATCH, "The checked artifact is not a regular file."
                )
            try:
                copied = _stable_read(artifact_fd, sink=staged_fd)
            except FileAccessError as exc:
                raise FileAccessError(
                    FileReason.ARTIFACT_MISMATCH, "The artifact changed while it was staged."
                ) from exc
        finally:
            os.close(artifact_fd)
        os.fsync(staged_fd)
        # Hash what is actually on disk in the staged file, not what was sent to it.
        staged = _stable_read(staged_fd)
        if copied != (expected_sha256, expected_size) or staged != copied:
            raise FileAccessError(
                FileReason.ARTIFACT_MISMATCH,
                "The artifact bytes differ from the checked artifact.",
            )


def _conflict() -> FileAccessError:
    return FileAccessError(
        FileReason.OUTPUT_CONFLICT, "A file with the output name already exists."
    )


def _lstat_or_none(name: str, dir_fd: int) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _unlink_quietly(name: str, dir_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def sha256_file(path: Path | str) -> tuple[str, int]:
    """Return the SHA-256 and size of a regular file read without following symlinks."""
    _require_platform()
    fd = os.open(path, _open_flags())
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FileAccessError(FileReason.ARTIFACT_MISMATCH, "Not a regular file.")
        return _stable_read(fd)
    finally:
        os.close(fd)
