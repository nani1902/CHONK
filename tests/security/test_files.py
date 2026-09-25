"""CHONK-006: file grants and race-safe publication.

Fixtures are synthetic byte strings; this module never parses PDF content.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from chonk.runtime import files
from chonk.runtime.files import (
    FileAccessError,
    FileReason,
    GrantRegistry,
    Overwrite,
    sha256_file,
    validate_output_name,
)

SENTINEL = "CHONK_SECRET_SENTINEL_7f3a"
ORIGINAL = b"%PDF-1.7\n% synthetic original " + SENTINEL.encode() + b"\n%%EOF\n"
CHECKED = b"%PDF-1.7\n% synthetic checked output\n%%EOF\n"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry(clock: FakeClock) -> GrantRegistry:
    return GrantRegistry(clock=clock)


@pytest.fixture
def session(registry: GrantRegistry):
    return registry.open_session()


@pytest.fixture
def layout(tmp_path: Path):
    """Source dir, destination dir, private workspace, and a checked artifact.

    Tests that deliberately edit the source restore it before returning.
    """
    source_dir = tmp_path / f"in-{SENTINEL}"
    dest_dir = tmp_path / "out"
    workspace = tmp_path / "work"
    for directory in (source_dir, dest_dir, workspace):
        directory.mkdir()
    source = source_dir / f"statement-{SENTINEL}.pdf"
    source.write_bytes(ORIGINAL)
    artifact = workspace / "candidate.pdf"
    artifact.write_bytes(CHECKED)
    yield {
        "root": tmp_path,
        "source": source,
        "dest": dest_dir,
        "workspace": workspace,
        "artifact": artifact,
    }
    # Every test must leave the granted original's bytes untouched.
    assert not source.is_symlink()
    assert source.read_bytes() == ORIGINAL


def checked(layout) -> dict:
    sha, size = sha256_file(layout["artifact"])
    return {"expected_sha256": sha, "expected_size": size}


def assert_reason(excinfo, reason: FileReason) -> None:
    assert excinfo.value.reason is reason
    assert excinfo.value.reason_code == reason.value
    assert SENTINEL not in str(excinfo.value)
    assert "/" not in str(excinfo.value)


# Grants and session scope ------------------------------------------------------


def test_grant_ids_are_opaque_and_path_free(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["dest"])
    for value in (grant.grant_id, dest.grant_id, repr(grant), repr(dest)):
        assert SENTINEL not in value
        assert str(layout["root"]) not in value
    assert grant.grant_id != registry.grant_file(session, layout["source"]).grant_id


def test_cross_session_handles_are_rejected_like_unknown_ids(registry, session, layout):
    other = registry.open_session()
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["dest"])

    with pytest.raises(FileAccessError) as foreign:
        registry.snapshot_source(other, grant.grant_id, layout["workspace"])
    with pytest.raises(FileAccessError) as unknown:
        registry.snapshot_source(other, "file_does_not_exist", layout["workspace"])
    assert_reason(foreign, FileReason.ACCESS_DENIED)
    assert str(foreign.value) == str(unknown.value)

    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(other, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert_reason(excinfo, FileReason.ACCESS_DENIED)
    with pytest.raises(FileAccessError):
        registry.revoke(other, grant.grant_id)
    assert list(layout["dest"].iterdir()) == []


def test_grant_kinds_are_not_interchangeable(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["dest"])
    with pytest.raises(FileAccessError):
        registry.snapshot_source(session, dest.grant_id, layout["workspace"])
    with pytest.raises(FileAccessError):
        registry.publish(session, grant.grant_id, "out.pdf", layout["artifact"], **checked(layout))


def test_revoked_grants_are_rejected_with_actionable_message(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["dest"])
    registry.revoke(session, grant.grant_id)
    registry.revoke(session, dest.grant_id)
    registry.revoke(session, dest.grant_id)  # idempotent

    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert_reason(excinfo, FileReason.ACCESS_DENIED)
    assert "revoked" in str(excinfo.value)
    with pytest.raises(FileAccessError):
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert list(layout["dest"].iterdir()) == []


def test_expired_grants_are_rejected(registry, session, layout, clock):
    grant = registry.grant_file(session, layout["source"], ttl_seconds=60)
    registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    clock.now += 60
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert_reason(excinfo, FileReason.ACCESS_DENIED)
    assert "expired" in str(excinfo.value)
    # Winding the clock back does not resurrect an expired grant.
    clock.now -= 60
    with pytest.raises(FileAccessError):
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])


def test_closing_a_session_revokes_its_grants(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    registry.close_session(session)
    with pytest.raises(FileAccessError):
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    with pytest.raises(FileAccessError):
        registry.grant_file(session, layout["source"])


def test_revocation_during_processing_blocks_publication(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["dest"])
    snapshot = registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    registry.revoke(session, grant.grant_id)
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(
            session, dest.grant_id, "out.pdf", layout["artifact"], sources=[snapshot], **checked(layout)
        )
    assert_reason(excinfo, FileReason.ACCESS_DENIED)
    assert list(layout["dest"].iterdir()) == []


# Source aliases and changes ------------------------------------------------------


def test_symlinked_source_is_rejected(registry, session, layout):
    link = layout["root"] / "alias.pdf"
    link.symlink_to(layout["source"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.grant_file(session, link)
    assert_reason(excinfo, FileReason.ACCESS_DENIED)


def test_hard_linked_source_is_rejected(registry, session, layout):
    os.link(layout["source"], layout["root"] / "alias.pdf")
    with pytest.raises(FileAccessError) as excinfo:
        registry.grant_file(session, layout["source"])
    assert_reason(excinfo, FileReason.ACCESS_DENIED)


@pytest.mark.parametrize("kind", ["directory", "fifo", "missing"])
def test_non_regular_sources_are_rejected(registry, session, layout, kind):
    target = layout["root"] / "special.pdf"
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)  # Must not block the open.
    with pytest.raises(FileAccessError) as excinfo:
        registry.grant_file(session, target)
    assert_reason(excinfo, FileReason.ACCESS_DENIED)


def test_symlinked_parent_directory_is_canonicalized(registry, session, layout):
    parent_link = layout["root"] / "linked-parent"
    parent_link.symlink_to(layout["source"].parent)
    grant = registry.grant_file(session, parent_link / layout["source"].name)
    assert grant.path == layout["source"].resolve()
    # Retargeting the symlink later does not redirect the grant.
    parent_link.unlink()
    elsewhere = layout["root"] / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / layout["source"].name).write_bytes(b"other")
    parent_link.symlink_to(elsewhere)
    snapshot = registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert snapshot.path.read_bytes() == ORIGINAL


def test_snapshot_is_private_copy_of_granted_bytes(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    snapshot = registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert snapshot.path.read_bytes() == ORIGINAL
    assert snapshot.sha256 == hashlib.sha256(ORIGINAL).hexdigest()
    assert snapshot.size == len(ORIGINAL)
    assert os.stat(snapshot.path).st_mode & 0o777 == 0o600
    assert not os.path.samefile(snapshot.path, layout["source"])
    assert SENTINEL not in repr(snapshot)


def test_modified_source_is_rejected(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    stat_before = os.stat(layout["source"])
    # Same length, restored mtime: only the content hash can detect this.
    layout["source"].write_bytes(ORIGINAL.replace(b"synthetic", b"SYNTHETIC"))
    os.utime(layout["source"], ns=(stat_before.st_atime_ns, stat_before.st_mtime_ns))
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)
    assert list(layout["workspace"].glob("source-*")) == []
    layout["source"].write_bytes(ORIGINAL)


def test_replaced_source_is_rejected(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    replacement = layout["root"] / "replacement.pdf"
    replacement.write_bytes(ORIGINAL)  # Identical bytes, different file.
    moved = layout["root"] / "moved-original.pdf"
    os.rename(layout["source"], moved)
    os.rename(replacement, layout["source"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)
    assert moved.read_bytes() == ORIGINAL


def test_source_swapped_for_symlink_is_rejected(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    secret = layout["root"] / "secret.pdf"
    secret.write_bytes(b"not granted")
    kept = layout["root"] / "kept.pdf"
    os.rename(layout["source"], kept)
    layout["source"].symlink_to(secret)
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)
    assert list(layout["workspace"].glob("source-*")) == []
    layout["source"].unlink()
    os.rename(kept, layout["source"])


def test_hard_link_added_after_grant_is_rejected(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    os.link(layout["source"], layout["root"] / "later-alias.pdf")
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)


def test_source_changed_while_reading_is_rejected(registry, session, layout, monkeypatch):
    grant = registry.grant_file(session, layout["source"])
    real_read = os.read
    appended = False

    def read_then_append(fd, count):
        nonlocal appended
        data = real_read(fd, count)
        if not appended:
            appended = True
            with open(layout["source"], "ab") as handle:
                handle.write(b"appended during read")
        return data

    monkeypatch.setattr(files.os, "read", read_then_append)
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    monkeypatch.undo()
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)
    assert list(layout["workspace"].glob("source-*")) == []
    layout["source"].write_bytes(ORIGINAL)


def test_source_rewritten_while_granting_is_rejected(registry, session, layout, monkeypatch):
    """No earlier hash exists at grant time, so only the stable-read check applies."""
    real_read = os.read
    rewritten = False

    def read_then_rewrite(fd, count):
        nonlocal rewritten
        data = real_read(fd, count)
        if not rewritten:
            rewritten = True
            layout["source"].write_bytes(ORIGINAL.upper())  # Same length.
        return data

    monkeypatch.setattr(files.os, "read", read_then_rewrite)
    with pytest.raises(FileAccessError) as excinfo:
        registry.grant_file(session, layout["source"])
    monkeypatch.undo()
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)
    layout["source"].write_bytes(ORIGINAL)


def test_snapshot_size_limit(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.snapshot_source(session, grant.grant_id, layout["workspace"], max_bytes=10)
    assert_reason(excinfo, FileReason.RESOURCE_LIMIT_EXCEEDED)
    assert list(layout["workspace"].glob("source-*")) == []


def test_source_changed_during_processing_blocks_publication(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["dest"])
    snapshot = registry.snapshot_source(session, grant.grant_id, layout["workspace"])
    layout["source"].write_bytes(ORIGINAL + b"edited")
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(
            session, dest.grant_id, "out.pdf", layout["artifact"], sources=[snapshot], **checked(layout)
        )
    assert_reason(excinfo, FileReason.SOURCE_CHANGED)
    assert list(layout["dest"].iterdir()) == []
    layout["source"].write_bytes(ORIGINAL)


# Output names and destinations ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../escape.pdf",
        "../../etc/passwd.pdf",
        "sub/out.pdf",
        "/abs/out.pdf",
        "..\\escape.pdf",
        ".hidden.pdf",
        "out.txt",
        "out.pdf.sh",
        ".pdf",
        "out\x00.pdf",
        "out\n.pdf",
        " out.pdf",
        "out.pdf ",
        "con.pdf",
        "a:b.pdf",
        "x" * 300 + ".pdf",
    ],
)
def test_unsafe_output_names_are_rejected(registry, session, layout, name):
    dest = registry.grant_destination(session, layout["dest"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, name, layout["artifact"], **checked(layout))
    assert excinfo.value.reason is FileReason.ACCESS_DENIED
    assert list(layout["dest"].iterdir()) == []
    assert not (layout["root"] / "escape.pdf").exists()


@pytest.mark.parametrize("name", ["out.pdf", "Report 2026 (final).PDF", "résumé.pdf"])
def test_safe_output_names_are_accepted(name):
    assert validate_output_name(name) == name


def test_symlinked_destination_directory_is_rejected(registry, session, layout):
    link = layout["root"] / "dest-link"
    link.symlink_to(layout["dest"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.grant_destination(session, link)
    assert_reason(excinfo, FileReason.ACCESS_DENIED)


def test_replaced_destination_directory_is_rejected(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    os.rename(layout["dest"], layout["root"] / "old-out")
    swapped = layout["root"] / "swapped"
    swapped.mkdir()
    (layout["dest"]).symlink_to(swapped)
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert_reason(excinfo, FileReason.ACCESS_DENIED)
    assert list(swapped.iterdir()) == []

    layout["dest"].unlink()
    layout["dest"].mkdir()  # New directory at the same path: different identity.
    with pytest.raises(FileAccessError):
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert list(layout["dest"].iterdir()) == []


# Publication ---------------------------------------------------------------------


def test_publishes_exact_checked_bytes(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    result = registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    output = layout["dest"] / "out.pdf"
    assert result.path == output.resolve()
    assert output.read_bytes() == CHECKED
    assert result.sha256 == hashlib.sha256(CHECKED).hexdigest()
    assert result.size == len(CHECKED)
    assert not result.replaced_existing
    assert os.stat(output).st_mode & 0o777 == 0o600
    assert os.stat(output).st_nlink == 1
    assert [p.name for p in layout["dest"].iterdir()] == ["out.pdf"]  # No staged leftovers.


def test_artifact_bytes_that_differ_from_checked_bytes_are_refused(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    expected = checked(layout)
    layout["artifact"].write_bytes(CHECKED.replace(b"checked", b"CHANGED"))  # Same length.
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **expected)
    assert_reason(excinfo, FileReason.ARTIFACT_MISMATCH)
    assert list(layout["dest"].iterdir()) == []


def test_ceiling_is_rechecked(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(
            session, dest.grant_id, "out.pdf", layout["artifact"], max_bytes=len(CHECKED) - 1, **checked(layout)
        )
    assert_reason(excinfo, FileReason.ARTIFACT_MISMATCH)
    assert list(layout["dest"].iterdir()) == []


def test_symlinked_artifact_is_refused(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    link = layout["workspace"] / "link.pdf"
    link.symlink_to(layout["artifact"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", link, **checked(layout))
    assert_reason(excinfo, FileReason.ARTIFACT_MISMATCH)


def test_existing_output_is_not_overwritten_by_default(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"], allow_replace=True)
    existing = layout["dest"] / "out.pdf"
    existing.write_bytes(b"someone else's file")
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert_reason(excinfo, FileReason.OUTPUT_CONFLICT)
    assert existing.read_bytes() == b"someone else's file"
    assert [p.name for p in layout["dest"].iterdir()] == ["out.pdf"]


def test_dangling_symlink_at_output_name_is_not_followed(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    target = layout["root"] / "planted-target.pdf"
    (layout["dest"] / "out.pdf").symlink_to(target)
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert_reason(excinfo, FileReason.OUTPUT_CONFLICT)
    assert not target.exists()


@pytest.mark.parametrize("strategy", ["rename_noreplace", "link"])
def test_concurrently_created_output_is_not_overwritten(registry, session, layout, monkeypatch, strategy):
    """An output created after the pre-check but before publication survives."""
    dest = registry.grant_destination(session, layout["dest"], allow_replace=True)
    if strategy == "link":
        monkeypatch.setattr(files, "_rename_noreplace", _unsupported)
    real_stage = GrantRegistry._stage_artifact

    def stage_then_race(*args, **kwargs):
        real_stage(*args, **kwargs)
        (layout["dest"] / "out.pdf").write_bytes(b"created concurrently")

    monkeypatch.setattr(GrantRegistry, "_stage_artifact", staticmethod(stage_then_race))
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert_reason(excinfo, FileReason.OUTPUT_CONFLICT)
    assert (layout["dest"] / "out.pdf").read_bytes() == b"created concurrently"
    assert [p.name for p in layout["dest"].iterdir()] == ["out.pdf"]


def test_link_fallback_publishes(registry, session, layout, monkeypatch):
    monkeypatch.setattr(files, "_rename_noreplace", _unsupported)
    dest = registry.grant_destination(session, layout["dest"])
    registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert (layout["dest"] / "out.pdf").read_bytes() == CHECKED
    assert os.stat(layout["dest"] / "out.pdf").st_nlink == 1
    assert [p.name for p in layout["dest"].iterdir()] == ["out.pdf"]


def test_fails_closed_without_atomic_no_clobber_support(registry, session, layout, monkeypatch):
    monkeypatch.setattr(files, "_rename_noreplace", _unsupported)
    monkeypatch.setattr(files, "_link_noreplace", _unsupported)
    dest = registry.grant_destination(session, layout["dest"])
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(session, dest.grant_id, "out.pdf", layout["artifact"], **checked(layout))
    assert_reason(excinfo, FileReason.PLATFORM_UNSUPPORTED)
    assert list(layout["dest"].iterdir()) == []


def test_parallel_publishers_have_exactly_one_winner(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    artifacts = []
    for index in range(8):
        path = layout["workspace"] / f"candidate-{index}.pdf"
        path.write_bytes(CHECKED + str(index).encode())
        artifacts.append(path)
    outcomes: list[object] = [None] * len(artifacts)
    barrier = threading.Barrier(len(artifacts))

    def publish(index: int) -> None:
        sha, size = sha256_file(artifacts[index])
        barrier.wait()
        try:
            outcomes[index] = registry.publish(
                session, dest.grant_id, "out.pdf", artifacts[index], expected_sha256=sha, expected_size=size
            )
        except FileAccessError as exc:
            outcomes[index] = exc.reason

    threads = [threading.Thread(target=publish, args=(i,)) for i in range(len(artifacts))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [i for i, outcome in enumerate(outcomes) if not isinstance(outcome, FileReason)]
    assert len(winners) == 1
    assert all(o is FileReason.OUTPUT_CONFLICT for i, o in enumerate(outcomes) if i != winners[0])
    assert (layout["dest"] / "out.pdf").read_bytes() == artifacts[winners[0]].read_bytes()
    assert [p.name for p in layout["dest"].iterdir()] == ["out.pdf"]


# Explicit overwrite policy -----------------------------------------------------------


def test_replace_requires_grant_permission(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    existing = layout["dest"] / "out.pdf"
    existing.write_bytes(b"keep me")
    for _ in range(3):  # Retries do not widen scope.
        with pytest.raises(FileAccessError) as excinfo:
            registry.publish(
                session, dest.grant_id, "out.pdf", layout["artifact"], overwrite=Overwrite.REPLACE, **checked(layout)
            )
        assert_reason(excinfo, FileReason.ACCESS_DENIED)
    assert existing.read_bytes() == b"keep me"


def test_replace_with_permission_and_explicit_request(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"], allow_replace=True)
    existing = layout["dest"] / "out.pdf"
    existing.write_bytes(b"previous output")
    result = registry.publish(
        session, dest.grant_id, "out.pdf", layout["artifact"], overwrite="replace", **checked(layout)
    )
    assert result.replaced_existing
    assert existing.read_bytes() == CHECKED
    assert [p.name for p in layout["dest"].iterdir()] == ["out.pdf"]


def test_replace_never_touches_a_granted_source(registry, session, layout):
    grant = registry.grant_file(session, layout["source"])
    dest = registry.grant_destination(session, layout["source"].parent, allow_replace=True)
    with pytest.raises(FileAccessError) as excinfo:
        registry.publish(
            session, dest.grant_id, layout["source"].name, layout["artifact"], overwrite="replace", **checked(layout)
        )
    assert_reason(excinfo, FileReason.ACCESS_DENIED)

    # Also through a hard link to the source created after the grant, and after revocation.
    alias = layout["source"].parent / "alias.pdf"
    os.link(layout["source"], alias)
    registry.revoke(session, grant.grant_id)
    with pytest.raises(FileAccessError):
        registry.publish(session, dest.grant_id, "alias.pdf", layout["artifact"], overwrite="replace", **checked(layout))
    assert alias.read_bytes() == ORIGINAL
    alias.unlink()


def test_replace_refuses_non_regular_targets(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"], allow_replace=True)
    target = layout["root"] / "target.pdf"
    target.write_bytes(b"linked target")
    (layout["dest"] / "link.pdf").symlink_to(target)
    (layout["dest"] / "dir.pdf").mkdir()
    for name in ("link.pdf", "dir.pdf"):
        with pytest.raises(FileAccessError) as excinfo:
            registry.publish(session, dest.grant_id, name, layout["artifact"], overwrite="replace", **checked(layout))
        assert_reason(excinfo, FileReason.OUTPUT_CONFLICT)
    assert target.read_bytes() == b"linked target"
    assert (layout["dest"] / "link.pdf").is_symlink()


def test_failed_publication_leaves_no_staged_files(registry, session, layout):
    dest = registry.grant_destination(session, layout["dest"])
    with pytest.raises(FileAccessError):
        registry.publish(
            session, dest.grant_id, "out.pdf", layout["artifact"], expected_sha256="0" * 64, expected_size=len(CHECKED)
        )
    assert list(layout["dest"].iterdir()) == []


def _unsupported(*_args, **_kwargs):
    raise files._NoReplaceUnsupported
