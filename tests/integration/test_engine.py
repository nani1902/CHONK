"""Engine behavior with a controlled backend; Ghostscript is not required."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from chonk import (
    AttemptStarted,
    CandidateMeasured,
    CompressionError,
    CompressionRequest,
    InputInspected,
    InvalidRequestError,
    OutputExistsError,
    ReasonCode,
    ResultStatus,
    compress_pdf,
)
from chonk import engine
from chonk.inspection import PageKind
from chonk.policies import DEFAULT_POLICY_ID, POLICIES
from conftest import PaddingBackend, make_image_pdf, sha256
from corpus import FIXTURES
from support import FileState, copy_fixture


def rank_padding(profile) -> int:
    # Higher-fidelity profiles are larger, as in the usual case.
    return int(profile.clarity_rank * 100_000)


def request(source: Path, output: Path, target: int, **overrides) -> CompressionRequest:
    return CompressionRequest(source=source, output=output, target_bytes=target, **overrides)


def test_ready_result_respects_ceiling_and_protects_source(image_pdf, unpadded_size, tmp_path, capfd):
    source_hash = sha256(image_pdf)
    output = tmp_path / "out" / "result.pdf"
    target = unpadded_size + 60_000
    backend = PaddingBackend(rank_padding)
    events = []

    result = compress_pdf(request(image_pdf, output, target), backend=backend, on_progress=events.append)

    assert result.status is ResultStatus.READY
    assert result.output == output.resolve()
    assert output.stat().st_size == result.selected.size_bytes <= target
    assert len(PdfReader(str(output)).pages) == result.page_count == 2
    assert sha256(image_pdf) == source_hash
    assert result.source_bytes == image_pdf.stat().st_size
    assert result.target_bytes == target
    # Every attempt is returned, in order, matching what the backend ran.
    assert [a.profile for a in result.attempts] == backend.calls
    assert result.selected in result.attempts
    assert result.smallest == min(result.attempts, key=lambda a: a.size_bytes)
    # Nothing is printed; progress arrives as typed events.
    assert capfd.readouterr() == ("", "")
    assert isinstance(events[0], InputInspected)
    assert events[0].page_count == 2 and events[0].target_bytes == target
    pairs = events[1:]
    assert len(pairs) == 2 * result.attempt_count
    for started, measured in zip(pairs[::2], pairs[1::2]):
        assert isinstance(started, AttemptStarted) and isinstance(measured, CandidateMeasured)
        assert started.profile == measured.attempt.profile
    # Only the published output is left beside it; staging files are removed.
    assert list(output.parent.iterdir()) == [output]


def test_first_profile_short_circuits(image_pdf, unpadded_size, tmp_path):
    backend = PaddingBackend(lambda _profile: 0)
    result = compress_pdf(
        request(image_pdf, tmp_path / "out.pdf", unpadded_size), backend=backend
    )
    assert result.status is ResultStatus.READY
    assert result.attempt_count == 1 and result.selected.profile_index == 0


def test_target_not_met_returns_data_and_writes_nothing(image_pdf, unpadded_size, tmp_path):
    output = tmp_path / "out" / "result.pdf"
    backend = PaddingBackend(rank_padding)
    result = compress_pdf(request(image_pdf, output, unpadded_size), backend=backend)

    assert result.status is ResultStatus.TARGET_NOT_MET
    assert result.output is None and result.selected is None
    assert result.attempt_count == 2
    assert result.smallest.size_bytes > unpadded_size
    assert not output.exists()
    assert list(output.parent.iterdir()) == []


def test_existing_output_is_not_replaced_by_default(image_pdf, unpadded_size, tmp_path):
    output = tmp_path / "out.pdf"
    output.write_bytes(b"keep me")
    backend = PaddingBackend(rank_padding)
    with pytest.raises(OutputExistsError) as info:
        compress_pdf(request(image_pdf, output, unpadded_size + 60_000), backend=backend)
    assert info.value.path == output.resolve()
    assert output.read_bytes() == b"keep me"
    assert backend.calls == []


def test_overwrite_replaces_existing_output(image_pdf, unpadded_size, tmp_path):
    output = tmp_path / "out.pdf"
    output.write_bytes(b"old")
    result = compress_pdf(
        request(image_pdf, output, unpadded_size + 60_000, overwrite=True),
        backend=PaddingBackend(rank_padding),
    )
    assert result.status is ResultStatus.READY
    assert output.stat().st_size == result.selected.size_bytes


@pytest.mark.parametrize(
    ("output_name", "message"),
    [("source.pdf", "different from the input"), ("out.txt", "must end in .pdf")],
)
def test_rejects_invalid_output_paths(image_pdf, tmp_path, output_name, message):
    source_hash = sha256(image_pdf)
    with pytest.raises(CompressionError, match=message):
        compress_pdf(
            request(image_pdf, tmp_path / output_name, 10_000_000),
            backend=PaddingBackend(rank_padding),
        )
    assert sha256(image_pdf) == source_hash


def test_missing_source_raises_os_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        compress_pdf(
            request(tmp_path / "missing.pdf", tmp_path / "out.pdf", 1_000),
            backend=PaddingBackend(rank_padding),
        )


def test_encrypted_source_is_blocked_before_compression(tmp_path):
    plain = make_image_pdf(tmp_path / "plain.pdf")
    writer = PdfWriter(clone_from=str(plain))
    writer.encrypt("secret")
    encrypted = tmp_path / "encrypted.pdf"
    with encrypted.open("wb") as stream:
        writer.write(stream)
    backend = PaddingBackend(rank_padding)
    output = tmp_path / "out.pdf"
    result = compress_pdf(request(encrypted, output, 10_000_000), backend=backend)
    assert result.status is ResultStatus.BLOCKED
    assert ReasonCode.ENCRYPTED_INPUT in result.reason_codes
    assert backend.calls == [] and result.attempts == () and result.smallest is None
    assert not output.exists()


def test_candidate_with_changed_page_count_fails(image_pdf, tmp_path):
    class DroppingBackend:
        def compress(self, source, output, profile, *, timeout):
            writer = PdfWriter()
            writer.add_page(PdfReader(str(source)).pages[0])
            with output.open("wb") as stream:
                writer.write(stream)

    output = tmp_path / "out.pdf"
    target = image_pdf.stat().st_size - 1  # Below the source, so the search runs.
    with pytest.raises(CompressionError, match="page count changed"):
        compress_pdf(request(image_pdf, output, target), backend=DroppingBackend())
    assert not output.exists()


def test_default_backend_reports_missing_ghostscript(image_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr("chonk.backends.ghostscript.shutil.which", lambda _name: None)
    target = image_pdf.stat().st_size - 1  # Below the source, so a backend is needed.
    with pytest.raises(CompressionError, match="Ghostscript was not found"):
        compress_pdf(request(image_pdf, tmp_path / "out.pdf", target))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"target_bytes": 0}, "whole number of bytes from 1"),
        ({"target_bytes": -5}, "whole number of bytes from 1"),
        ({"target_bytes": True}, "whole number of bytes from 1"),
        ({"target_bytes": 1.5}, "whole number of bytes from 1"),
        ({"target_bytes": 10 * 1024**3 + 1}, "whole number of bytes from 1"),
        ({"min_dpi": 0}, "DPI values must be integers from 1"),
        ({"max_dpi": 2401}, "DPI values must be integers from 1"),
        ({"min_dpi": 700}, "minimum DPI cannot be greater"),
        ({"max_attempts": 1}, "from 2 to 64"),
        ({"max_attempts": 65}, "from 2 to 64"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"timeout_seconds": 86_401}, "timeout"),
        ({"comparison_dpi": 35}, "between 36 and 600"),
        ({"comparison_dpi": 601}, "between 36 and 600"),
        ({"max_dpi": "600"}, "max_dpi must be an integer"),
    ],
)
def test_invalid_requests_are_rejected_before_any_work(image_pdf, tmp_path, overrides, message):
    fields = {"target_bytes": 1_000_000, **overrides}
    backend = PaddingBackend(rank_padding)
    with pytest.raises(InvalidRequestError, match=message):
        compress_pdf(
            CompressionRequest(source=image_pdf, output=tmp_path / "out.pdf", **fields),
            backend=backend,
        )
    assert backend.calls == []


# --- Preflight (CHONK-005) ------------------------------------------------------------


EXPECTED_REASONS = {
    "encrypted-user-password": {ReasonCode.ENCRYPTED_INPUT, ReasonCode.INSPECTION_INCONCLUSIVE},
    "encrypted-owner-only": {ReasonCode.ENCRYPTED_INPUT},
    "signed-pkcs7": {ReasonCode.SIGNED_INPUT, ReasonCode.UNSUPPORTED_FEATURE},
    "orphan-javascript": {ReasonCode.INSPECTION_INCONCLUSIVE},
    "malformed-truncated": {ReasonCode.MALFORMED_INPUT},
    "malformed-not-pdf": {ReasonCode.MALFORMED_INPUT},
}


@pytest.mark.parametrize(
    "spec", [s for s in FIXTURES if s.preflight != "supported"], ids=lambda spec: spec.name
)
def test_unsupported_input_is_blocked_before_any_backend_runs(spec, corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, spec.name, tmp_path)
    before = FileState.of(source)
    output = tmp_path / "out.pdf"
    backend = PaddingBackend(rank_padding)
    events = []
    result = compress_pdf(
        request(source, output, 10 * source.stat().st_size), backend=backend, on_progress=events.append
    )
    assert result.status is ResultStatus.BLOCKED
    assert set(result.reason_codes) == EXPECTED_REASONS.get(spec.name, {ReasonCode.UNSUPPORTED_FEATURE})
    assert backend.calls == [] and events == []
    assert result.attempts == () and result.selected is None and result.smallest is None
    assert result.inspection is not None and result.preflight is not None
    assert not result.preflight.allowed
    assert result.policy_id == DEFAULT_POLICY_ID
    assert not output.exists()
    assert FileState.of(source) == before


def test_blocked_input_does_not_need_a_backend(corpus_dir, tmp_path, monkeypatch):
    """Without an explicit backend, preflight still runs before discovery."""

    def no_discovery(*_args, **_kwargs):
        raise AssertionError("the backend must not be discovered for a blocked input")

    monkeypatch.setattr(engine.GhostscriptBackend, "discover", no_discovery)
    source = copy_fixture(corpus_dir, "form-acroform", tmp_path)
    result = compress_pdf(request(source, tmp_path / "out.pdf", 10_000_000))
    assert result.status is ResultStatus.BLOCKED


def test_supported_input_carries_its_inspection(image_pdf, unpadded_size, tmp_path):
    result = compress_pdf(
        request(image_pdf, tmp_path / "out.pdf", unpadded_size + 60_000),
        backend=PaddingBackend(rank_padding),
    )
    assert result.status is ResultStatus.READY
    assert result.preflight.allowed and result.reason_codes == ()
    assert [page.kind for page in result.inspection.pages] == [PageKind.IMAGE_ONLY] * 2


def test_default_request_policy_is_the_default_policy():
    assert CompressionRequest(Path("a.pdf"), Path("b.pdf"), 1).policy_id == DEFAULT_POLICY_ID


@pytest.mark.parametrize("policy_id", ["no-such-policy-v1", "preserve-existing-text-v9", 7])
def test_unknown_policy_is_an_invalid_request(image_pdf, tmp_path, policy_id):
    backend = PaddingBackend(rank_padding)
    with pytest.raises(InvalidRequestError, match="[Pp]olicy"):
        compress_pdf(request(image_pdf, tmp_path / "out.pdf", 1_000_000, policy_id=policy_id), backend=backend)
    assert backend.calls == []


@pytest.mark.parametrize("policy_id", sorted(POLICIES))
def test_every_policy_blocks_the_same_unsupported_features(corpus_dir, tmp_path, policy_id):
    source = copy_fixture(corpus_dir, "annotations", tmp_path)
    result = compress_pdf(
        request(source, tmp_path / "out.pdf", 10_000_000, policy_id=policy_id),
        backend=PaddingBackend(rank_padding),
    )
    assert result.status is ResultStatus.BLOCKED and result.policy_id == policy_id
