"""CHONK-007: policy enforcement and the unchanged-source fast path.

Engine behavior with a controlled backend; Ghostscript is not required.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chonk import engine
from chonk.adapters.text import describe_blocked, describe_success
from chonk.contract import build_result
from chonk.models import (
    CheckId,
    CheckState,
    CompletionBasis,
    CompressionRequest,
    NextAction,
    OutputExistsError,
    ReasonCode,
    ResultStatus,
    SearchSummary,
)
from chonk.policies import DEFAULT_POLICY_ID, POLICIES, REQUIRE_SEARCHABLE_V1
from conftest import PaddingBackend
from corpus import FIXTURES
from support import FileState, copy_fixture

SUPPORTED = [spec for spec in FIXTURES if spec.preflight == "supported"]
UNSUPPORTED = [spec for spec in FIXTURES if spec.preflight != "supported"]
REQUIRE_SEARCHABLE = REQUIRE_SEARCHABLE_V1.policy_id
SCAN_POLICIES = sorted(set(POLICIES) - {REQUIRE_SEARCHABLE})

# Supported fixtures without a usable text layer, and how require-searchable-v1
# treats them. The catalog's has_text_layer flag says which fixtures belong here.
NO_TEXT_LAYER = {
    "image-scan": (CheckState.FAIL, ReasonCode.TEXT_LAYER_MISSING),
    "vector-only": (CheckState.FAIL, ReasonCode.TEXT_LAYER_MISSING),
    # Text objects whose characters have no Unicode value: the page cannot be
    # shown to carry a usable layer, nor shown to lack one.
    "text-unmapped": (CheckState.UNKNOWN, ReasonCode.VALIDATION_INCONCLUSIVE),
}


def rank_padding(profile) -> int:
    return int(profile.clarity_rank * 100_000)


def request(source: Path, output: Path, target: int, **overrides) -> CompressionRequest:
    return CompressionRequest(source=source, output=output, target_bytes=target, **overrides)


def allowed_under(spec, policy_id: str) -> bool:
    return policy_id != REQUIRE_SEARCHABLE or spec.name not in NO_TEXT_LAYER


ELIGIBLE = [
    pytest.param(spec, policy_id, id=f"{spec.name}-{policy_id}")
    for spec in SUPPORTED
    for policy_id in sorted(POLICIES)
    if allowed_under(spec, policy_id)
]


@pytest.fixture
def no_discovery(monkeypatch):
    """Fail if the engine tries to find Ghostscript."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("no backend may be discovered on this path")

    monkeypatch.setattr(engine.GhostscriptBackend, "discover", refuse)


def as_contract(result):
    """The engine result as a schema 1.0 result; raises if it breaks the contract."""
    ready = result.status is ResultStatus.READY
    return build_result(
        job_id="job_test",
        file_id="file_test",
        status=result.status,
        policy_id=result.policy_id,
        target_bytes=result.target_bytes,
        reason_codes=result.reason_codes,
        checks=dict(result.checks),
        completion_basis=result.completion_basis,
        output_bytes=result.output_bytes,
        artifact_id="artifact_test" if ready else None,
        search=SearchSummary(attempts=result.attempt_count, max_attempts=16) if ready else None,
    )


def test_the_corpus_marks_every_fixture_without_a_text_layer():
    assert {spec.name for spec in SUPPORTED if not spec.has_text_layer} == set(NO_TEXT_LAYER)


# --- Unchanged-source fast path -----------------------------------------------------


@pytest.mark.parametrize(("spec", "policy_id"), ELIGIBLE)
def test_eligible_source_that_fits_is_published_byte_for_byte(
    spec, policy_id, corpus_dir, tmp_path, no_discovery
):
    source = copy_fixture(corpus_dir, spec.name, tmp_path)
    before = FileState.of(source)
    output = tmp_path / "out" / "result.pdf"
    backend = PaddingBackend(rank_padding)
    events = []

    result = engine.compress_pdf(
        request(source, output, source.stat().st_size, policy_id=policy_id),
        backend=backend,
        on_progress=events.append,
    )

    assert result.status is ResultStatus.READY
    assert result.completion_basis is CompletionBasis.UNCHANGED_SOURCE and result.unchanged_source
    assert result.policy_id == policy_id and result.preflight.policy_id == policy_id
    assert output.read_bytes() == source.read_bytes()
    assert result.output == output.resolve() and result.output_bytes == before.size
    assert result.attempts == () and result.selected is None and result.smallest is None
    assert backend.calls == [] and events == []
    assert FileState.of(source) == before
    assert list(output.parent.iterdir()) == [output]
    # Every check the policy names is decided, and closed without review.
    policy = POLICIES[policy_id]
    assert set(result.checks) == set(policy.checks)
    assert all(policy.is_closed(check, state) for check, state in result.checks.items())
    contract = as_contract(result)
    assert contract.completion_basis is CompletionBasis.UNCHANGED_SOURCE
    assert contract.next_action is NextAction.USE_LOCAL_ARTIFACT


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("text-statement", CheckState.PASS),
        ("image-mixed", CheckState.PASS),
        ("blank-page", CheckState.PASS),
        ("image-scan", CheckState.NOT_APPLICABLE),
        ("vector-only", CheckState.NOT_APPLICABLE),
    ],
)
def test_unchanged_text_preservation_is_not_applicable_only_without_any_text(
    name, expected, corpus_dir, tmp_path
):
    source = copy_fixture(corpus_dir, name, tmp_path)
    result = engine.compress_pdf(
        request(source, tmp_path / "out.pdf", source.stat().st_size),
        backend=PaddingBackend(rank_padding),
    )
    assert result.unchanged_source
    assert result.checks[CheckId.EXTRACTED_TEXT] is expected


def test_one_byte_over_the_ceiling_runs_the_search(image_pdf, tmp_path):
    size = image_pdf.stat().st_size
    backend = PaddingBackend(lambda _profile: 0)
    result = engine.compress_pdf(request(image_pdf, tmp_path / "out.pdf", size - 1), backend=backend)
    assert result.status is ResultStatus.READY and not result.unchanged_source
    assert backend.calls and result.selected is not None
    assert result.output_bytes == result.selected.size_bytes < size


def test_compressed_result_claims_only_the_checks_it_ran(image_pdf, tmp_path):
    """No completion basis until the validators (CHONK-008/009) decide the rest."""
    result = engine.compress_pdf(
        request(image_pdf, tmp_path / "out.pdf", image_pdf.stat().st_size - 1),
        backend=PaddingBackend(lambda _profile: 0),
    )
    assert result.status is ResultStatus.READY
    assert result.completion_basis is None
    assert result.checks == {
        CheckId.SIZE_CEILING: CheckState.PASS,
        CheckId.FEATURE_SUPPORT: CheckState.PASS,
    }


def test_target_not_met_records_the_failed_size_check(image_pdf, unpadded_size, tmp_path):
    result = engine.compress_pdf(
        request(image_pdf, tmp_path / "out.pdf", unpadded_size), backend=PaddingBackend(rank_padding)
    )
    assert result.status is ResultStatus.TARGET_NOT_MET
    assert result.checks[CheckId.SIZE_CEILING] is CheckState.FAIL


def test_unchanged_copy_never_replaces_an_existing_output_by_default(corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, "already-small", tmp_path)
    output = tmp_path / "out.pdf"
    output.write_bytes(b"keep me")
    with pytest.raises(OutputExistsError):
        engine.compress_pdf(request(source, output, 1_000_000))
    assert output.read_bytes() == b"keep me"

    result = engine.compress_pdf(request(source, output, 1_000_000, overwrite=True))
    assert result.unchanged_source
    assert output.read_bytes() == source.read_bytes()


def test_unchanged_copy_is_described_as_such(corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, "already-small", tmp_path)
    result = engine.compress_pdf(request(source, tmp_path / "out.pdf", 1_000_000))
    message = describe_success(result)
    assert "copied unchanged" in message and "already within the ceiling" in message


# --- Unsupported input stays blocked, whatever its size ----------------------------


@pytest.mark.parametrize("policy_id", sorted(POLICIES))
@pytest.mark.parametrize("spec", UNSUPPORTED, ids=lambda spec: spec.name)
def test_unsupported_input_below_the_ceiling_is_still_blocked(
    spec, policy_id, corpus_dir, tmp_path, no_discovery
):
    source = copy_fixture(corpus_dir, spec.name, tmp_path)
    before = FileState.of(source)
    output = tmp_path / "out.pdf"
    backend = PaddingBackend(rank_padding)
    result = engine.compress_pdf(
        request(source, output, 10 * before.size, policy_id=policy_id), backend=backend
    )
    assert result.status is ResultStatus.BLOCKED and result.reason_codes
    assert result.completion_basis is None and result.output is None
    assert result.checks[CheckId.FEATURE_SUPPORT] is not CheckState.PASS
    assert result.policy_id == policy_id
    assert backend.calls == [] and not output.exists()
    assert FileState.of(source) == before
    as_contract(result)


# --- require-searchable-v1: pages without a text layer -----------------------------


@pytest.mark.parametrize("fits", [True, False], ids=["below-ceiling", "above-ceiling"])
@pytest.mark.parametrize("name", sorted(NO_TEXT_LAYER))
def test_require_searchable_blocks_pages_without_a_text_layer(
    name, fits, corpus_dir, tmp_path, no_discovery
):
    source = copy_fixture(corpus_dir, name, tmp_path)
    before = FileState.of(source)
    output = tmp_path / "out.pdf"
    backend = PaddingBackend(rank_padding)
    target = before.size if fits else before.size - 1
    result = engine.compress_pdf(
        request(source, output, target, policy_id=REQUIRE_SEARCHABLE), backend=backend
    )
    state, reason = NO_TEXT_LAYER[name]
    assert result.status is ResultStatus.BLOCKED
    assert result.reason_codes == (reason,)
    assert result.checks == {CheckId.FEATURE_SUPPORT: CheckState.PASS, CheckId.TEXT_LAYER: state}
    assert result.policy_id == REQUIRE_SEARCHABLE
    assert backend.calls == [] and not output.exists()
    assert FileState.of(source) == before
    assert as_contract(result).next_action is NextAction.HANDLE_MANUALLY


def test_blocked_scan_has_an_actionable_explanation(corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, "image-scan", tmp_path)
    result = engine.compress_pdf(
        request(source, tmp_path / "out.pdf", 10_000_000, policy_id=REQUIRE_SEARCHABLE)
    )
    assert result.preflight.text_layer.pages_without_text == (0, 1)
    message = describe_blocked(result)
    assert "Pages 1 and 2 have no text layer" in message
    assert REQUIRE_SEARCHABLE in message
    assert "run OCR first, or choose a policy that allows scans" in message
    assert "No output was written" in message


def test_undecidable_text_layer_names_the_pages(corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, "text-unmapped", tmp_path)
    result = engine.compress_pdf(
        request(source, tmp_path / "out.pdf", 10_000_000, policy_id=REQUIRE_SEARCHABLE)
    )
    assert "could not confirm a usable text layer on page 1" in describe_blocked(result)


@pytest.mark.parametrize("policy_id", SCAN_POLICIES)
@pytest.mark.parametrize("name", sorted(NO_TEXT_LAYER))
def test_other_policies_accept_pages_without_a_text_layer(name, policy_id, corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, name, tmp_path)
    result = engine.compress_pdf(
        request(source, tmp_path / "out.pdf", source.stat().st_size, policy_id=policy_id)
    )
    assert result.status is ResultStatus.READY and result.unchanged_source
    assert CheckId.TEXT_LAYER not in result.checks
    assert result.policy_id == policy_id


def test_scan_above_the_ceiling_is_compressed_under_its_own_policy(image_pdf, tmp_path):
    backend = PaddingBackend(lambda _profile: 0)
    result = engine.compress_pdf(
        request(image_pdf, tmp_path / "out.pdf", image_pdf.stat().st_size - 1, policy_id="scan-review-v1"),
        backend=backend,
    )
    assert result.status is ResultStatus.READY and backend.calls
    assert result.policy_id == "scan-review-v1"


# --- No fallback changes the policy -------------------------------------------------


@pytest.mark.parametrize("policy_id", sorted(POLICIES))
@pytest.mark.parametrize("spec", FIXTURES, ids=lambda spec: spec.name)
def test_every_result_reports_the_requested_policy(spec, policy_id, corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, spec.name, tmp_path)
    result = engine.compress_pdf(
        request(source, tmp_path / "out.pdf", source.stat().st_size, policy_id=policy_id),
        backend=PaddingBackend(rank_padding),
    )
    assert result.policy_id == policy_id
    assert result.preflight.policy_id == policy_id
    expected = spec.preflight == "supported" and allowed_under(spec, policy_id)
    assert (result.status is ResultStatus.READY) is expected
    assert result.unchanged_source is expected
    as_contract(result)


def test_default_policy_does_not_block_scans(corpus_dir, tmp_path):
    source = copy_fixture(corpus_dir, "image-scan", tmp_path)
    result = engine.compress_pdf(request(source, tmp_path / "out.pdf", 10_000_000))
    assert result.policy_id == DEFAULT_POLICY_ID
    assert result.status is ResultStatus.READY


# --- One snapshot: the source may not change under the job -------------------------


def test_source_changed_after_inspection_blocks_the_unchanged_copy(corpus_dir, tmp_path, monkeypatch):
    source = copy_fixture(corpus_dir, "already-small", tmp_path)
    real_inspect = engine.inspect_pdf

    def inspect_then_modify(path):
        report = real_inspect(path)
        with source.open("ab") as stream:
            stream.write(b"\n% appended after inspection\n")
        return report

    monkeypatch.setattr(engine, "inspect_pdf", inspect_then_modify)
    output = tmp_path / "out.pdf"
    result = engine.compress_pdf(request(source, output, 1_000_000))
    assert result.status is ResultStatus.BLOCKED
    assert result.reason_codes == (ReasonCode.SOURCE_CHANGED,)
    assert not output.exists()
    assert as_contract(result).next_action is NextAction.RETRY
    message = describe_blocked(result)
    assert "changed while CHONK was working on it" in message
    assert "the original is unchanged" not in message


def test_backend_reads_a_snapshot_and_a_changed_source_is_not_published(image_pdf, tmp_path):
    original = image_pdf.resolve()
    seen = []

    class ModifyingBackend(PaddingBackend):
        def compress(self, source, output, profile, *, timeout):
            seen.append(Path(source))
            with original.open("ab") as stream:
                stream.write(b"\n% changed during compression\n")
            super().compress(source, output, profile, timeout=timeout)

    output = tmp_path / "out.pdf"
    result = engine.compress_pdf(
        request(image_pdf, output, image_pdf.stat().st_size - 1),
        backend=ModifyingBackend(lambda _profile: 0),
    )
    assert seen and all(path != original for path in seen)
    assert result.status is ResultStatus.BLOCKED
    assert result.reason_codes == (ReasonCode.SOURCE_CHANGED,)
    assert result.attempt_count == len(seen)
    assert not output.exists()


def test_source_growing_during_the_snapshot_is_detected(corpus_dir, tmp_path, monkeypatch):
    source = copy_fixture(corpus_dir, "already-small", tmp_path)
    real_fstat = os.fstat
    calls: dict[int, int] = {}

    def fstat_while_growing(fd):
        # The engine checks the source descriptor before and after copying;
        # grow the file just before the second check.
        calls[fd] = calls.get(fd, 0) + 1
        if calls[fd] == 2:
            with source.open("ab") as stream:
                stream.write(b"\n% appended while copying\n")
        return real_fstat(fd)

    monkeypatch.setattr(engine.os, "fstat", fstat_while_growing)
    output = tmp_path / "out.pdf"
    result = engine.compress_pdf(request(source, output, 1_000_000))
    assert result.status is ResultStatus.BLOCKED
    assert result.reason_codes == (ReasonCode.SOURCE_CHANGED,)
    assert result.inspection is None and not output.exists()
    assert "changed while CHONK was working on it" in describe_blocked(result)


def test_job_directory_is_removed(corpus_dir, tmp_path, monkeypatch):
    seen = []
    real_snapshot = engine._snapshot_source

    def recording_snapshot(source, workdir):
        seen.append(workdir)
        return real_snapshot(source, workdir)

    monkeypatch.setattr(engine, "_snapshot_source", recording_snapshot)
    source = copy_fixture(corpus_dir, "already-small", tmp_path)
    engine.compress_pdf(request(source, tmp_path / "out.pdf", 1_000_000))
    assert seen and not seen[0].exists()
