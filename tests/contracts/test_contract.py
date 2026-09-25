"""Contract tests for the versioned request/result schema (1.0)."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path

import pytest

from chonk.contract import (
    ALLOWED_REASONS,
    accept_review,
    build_result,
    error_to_dict,
    json_schemas,
    next_action_for,
    reject_review,
    request_from_dict,
    request_to_dict,
    result_from_dict,
    result_to_dict,
    validate_result,
)
from chonk.models import (
    MAX_ATTEMPTS,
    MAX_DEADLINE_SECONDS,
    MAX_TARGET_BYTES,
    CheckId,
    CheckState,
    CompletionBasis,
    ContractError,
    ErrorCode,
    NextAction,
    ReasonCode,
    ResultStatus,
    ReviewDecision,
    SearchSummary,
)
from chonk.policies import POLICIES, PolicyDefinition

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "1.0"
EXAMPLES = SCHEMA_DIR / "examples"
RESULT_EXAMPLES = sorted(EXAMPLES.glob("result-*.json"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def example(name: str) -> dict:
    return load(EXAMPLES / f"{name}.json")


def schema_validator(name: str):
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft202012Validator(json_schemas()[name])


def expect_error(code: ErrorCode, field: str | None = None):
    class Expectation:
        def __enter__(self):
            self._ctx = pytest.raises(ContractError)
            self.info = self._ctx.__enter__()
            return self.info

        def __exit__(self, *exc):
            suppressed = self._ctx.__exit__(*exc)
            assert self.info.value.code is code, self.info.value
            if field is not None:
                assert self.info.value.field == field, self.info.value
            return suppressed

    return Expectation()


# --- Examples and schemas -----------------------------------------------------


def test_checked_in_schemas_match_generated():
    for name, schema in json_schemas().items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        assert path.exists(), f"run scripts/generate_schemas.py to create {path.name}"
        assert load(path) == schema, f"{path.name} is stale; run scripts/generate_schemas.py"


def test_generated_schemas_are_valid_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    for schema in json_schemas().values():
        jsonschema.Draft202012Validator.check_schema(schema)


def test_every_result_status_has_a_valid_example():
    statuses = {load(path)["status"] for path in RESULT_EXAMPLES}
    assert statuses == {status.value for status in ResultStatus}
    bases = {load(path).get("completion_basis") for path in RESULT_EXAMPLES} - {None}
    assert bases == {basis.value for basis in CompletionBasis}


@pytest.mark.parametrize("path", RESULT_EXAMPLES, ids=lambda p: p.stem)
def test_result_examples_round_trip_and_match_schema(path):
    data = load(path)
    result = result_from_dict(data)
    assert result_to_dict(result) == data
    assert json.loads(json.dumps(result_to_dict(result))) == data
    errors = list(schema_validator("result").iter_errors(data))
    assert not errors, errors


def test_request_example_round_trips_and_matches_schema():
    data = example("prepare-request")
    request = request_from_dict(data)
    assert request_to_dict(request) == data
    assert not list(schema_validator("prepare-request").iter_errors(data))


def test_error_example_is_what_chonk_emits():
    data = example("error-unsupported-policy-version")
    request = {**example("prepare-request"), "policy_id": "preserve-existing-text-v2"}
    with pytest.raises(ContractError) as info:
        request_from_dict(request)
    assert error_to_dict(info.value) == data
    assert not list(schema_validator("error").iter_errors(data))


def test_every_reason_code_belongs_to_a_status():
    used = set().union(*ALLOWED_REASONS.values())
    assert used == set(ReasonCode)


def test_result_fields_are_an_explicit_allowlist():
    # Guards the privacy boundary: adding a field (e.g. a path, name, or hash)
    # to the agent-facing result must be a deliberate, reviewed change.
    assert set(json_schemas()["result"]["properties"]) == {
        "schema_version",
        "job_id",
        "file_id",
        "status",
        "reason_codes",
        "completion_basis",
        "policy_id",
        "target_bytes",
        "output_bytes",
        "artifact_id",
        "checks",
        "search",
        "review",
        "limitations",
        "next_action",
    }


# --- Versions -----------------------------------------------------------------


@pytest.mark.parametrize("version", ["0.9", "1.1", "2.0", "1", ""])
def test_unknown_schema_versions_are_rejected(version):
    request = {**example("prepare-request"), "schema_version": version}
    with expect_error(ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "schema_version"):
        request_from_dict(request)
    result = {**example("result-ready"), "schema_version": version}
    with expect_error(ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "schema_version"):
        result_from_dict(result)


def test_newer_schema_is_reported_before_its_unknown_fields():
    request = {**example("prepare-request"), "schema_version": "2.0", "new_field": 1}
    with expect_error(ErrorCode.UNSUPPORTED_SCHEMA_VERSION):
        request_from_dict(request)


@pytest.mark.parametrize("value", [None, 1.0, 1])
def test_missing_or_mistyped_schema_version_is_invalid(value):
    request = example("prepare-request")
    if value is None:
        del request["schema_version"]
    else:
        request["schema_version"] = value
    with expect_error(ErrorCode.INVALID_REQUEST, "schema_version"):
        request_from_dict(request)


@pytest.mark.parametrize(
    ("policy_id", "code"),
    [
        ("preserve-existing-text-v2", ErrorCode.UNSUPPORTED_POLICY_VERSION),
        ("scan-review-v0", ErrorCode.UNKNOWN_POLICY),
        ("require-searchable-v10", ErrorCode.UNSUPPORTED_POLICY_VERSION),
        ("preserve-existing-text", ErrorCode.UNKNOWN_POLICY),
        ("compress-anything-v1", ErrorCode.UNKNOWN_POLICY),
        ("PRESERVE-EXISTING-TEXT-V1", ErrorCode.UNKNOWN_POLICY),
    ],
)
def test_unknown_policies_and_versions_are_rejected(policy_id, code):
    with expect_error(code, "policy_id"):
        request_from_dict({**example("prepare-request"), "policy_id": policy_id})
    with expect_error(code, "policy_id"):
        result_from_dict({**example("result-blocked"), "policy_id": policy_id})


# --- Request limits -------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [0, -1, True, False, 1.5, 2_000_000.0, "2000000", MAX_TARGET_BYTES + 1, None]
)
def test_target_bytes_must_be_a_bounded_positive_integer(value):
    with expect_error(ErrorCode.INVALID_REQUEST, "target_bytes"):
        request_from_dict({**example("prepare-request"), "target_bytes": value})


@pytest.mark.parametrize("value", [1, MAX_TARGET_BYTES])
def test_target_bytes_bounds_are_inclusive(value):
    request = request_from_dict({**example("prepare-request"), "target_bytes": value})
    assert request.target_bytes == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deadline_seconds", 0),
        ("deadline_seconds", MAX_DEADLINE_SECONDS + 1),
        ("deadline_seconds", 60.0),
        ("max_attempts", 1),
        ("max_attempts", MAX_ATTEMPTS + 1),
        ("max_attempts", True),
        ("file_id", "../statement.pdf"),
        ("file_id", "/home/user/statement.pdf"),
        ("file_id", ""),
        ("file_id", "x" * 129),
        ("file_id", 12),
        ("idempotency_key", "has space"),
        ("idempotency_key", "k" * 129),
    ],
)
def test_request_fields_are_bounded(field, value):
    with expect_error(ErrorCode.INVALID_REQUEST, field):
        request_from_dict({**example("prepare-request"), field: value})


def test_request_defaults_and_nulls():
    data = example("prepare-request")
    del data["max_attempts"]
    data["idempotency_key"] = None
    request = request_from_dict(data)
    assert request.max_attempts == 16
    assert request.idempotency_key is None


def test_request_rejects_unknown_fields_without_echoing_them():
    data = {**example("prepare-request"), "SECRET_MARKER_path": "/tmp/SECRET_MARKER.pdf"}
    with expect_error(ErrorCode.INVALID_REQUEST) as info:
        request_from_dict(data)
    assert "SECRET_MARKER" not in str(info.value)
    assert "SECRET_MARKER" not in json.dumps(error_to_dict(info.value))


def test_rejected_values_are_not_echoed():
    data = {**example("prepare-request"), "file_id": "SECRET_MARKER/statement.pdf"}
    with pytest.raises(ContractError) as info:
        request_from_dict(data)
    assert "SECRET_MARKER" not in json.dumps(error_to_dict(info.value))


@pytest.mark.parametrize("data", [None, [], "request", 5])
def test_request_must_be_an_object(data):
    with expect_error(ErrorCode.INVALID_REQUEST):
        request_from_dict(data)


# --- Ready and review rules -------------------------------------------------------


def _ready(policy: PolicyDefinition, checks, basis: CompletionBasis, accepted=()):
    return build_result(
        job_id="job_1",
        file_id="file_1",
        status=ResultStatus.READY,
        policy_id=policy.policy_id,
        target_bytes=1000,
        output_bytes=900,
        artifact_id="artifact_1",
        checks=checks,
        completion_basis=basis,
        review=ReviewDecision(tuple(accepted)) if basis is CompletionBasis.HUMAN_REVIEW else None,
    )


def _needs_review(policy: PolicyDefinition, checks, reasons):
    return build_result(
        job_id="job_1",
        file_id="file_1",
        status=ResultStatus.NEEDS_REVIEW,
        policy_id=policy.policy_id,
        target_bytes=1000,
        output_bytes=900,
        checks=checks,
        reason_codes=tuple(reasons),
    )


def _combinations(policy: PolicyDefinition):
    for states in itertools.product(CheckState, repeat=len(policy.checks)):
        checks = dict(zip(policy.checks, states))
        # Output bytes are within the target, so a size failure would be
        # inconsistent for reasons unrelated to the rule under test.
        if checks[CheckId.SIZE_CEILING] is CheckState.FAIL:
            continue
        yield checks


def _valid(build) -> bool:
    try:
        build()
    except ContractError:
        return False
    return True


@pytest.mark.parametrize("policy", POLICIES.values(), ids=lambda p: p.policy_id)
def test_ready_is_valid_only_when_required_checks_pass(policy):
    """Exhaustive: no combination with a required unknown or fail is ever ready,
    for any completion basis, and human review accepts exactly the open
    advisory checks."""
    for checks in _combinations(policy):
        hard_ok = all(policy.is_closed(c, checks[c]) for c in policy.required_checks)
        n_a_ok = all(
            s is not CheckState.NOT_APPLICABLE or c in policy.not_applicable_allowed
            for c, s in checks.items()
        )
        open_advisory = [c for c in policy.advisory_checks if not policy.is_closed(c, checks[c])]

        for basis in (CompletionBasis.AUTOMATED_CHECKS, CompletionBasis.UNCHANGED_SOURCE):
            valid = _valid(lambda: _ready(policy, checks, basis))
            assert valid == (hard_ok and n_a_ok and not open_advisory), (basis, checks)

        human = _valid(lambda: _ready(policy, checks, CompletionBasis.HUMAN_REVIEW, open_advisory))
        assert human == (hard_ok and n_a_ok and bool(open_advisory)), checks
        if not hard_ok:
            # Even accepting every check, required ones included, is refused.
            everything = [c for c in policy.checks if not policy.is_closed(c, checks[c])]
            assert not _valid(
                lambda: _ready(policy, checks, CompletionBasis.HUMAN_REVIEW, everything)
            ), checks


@pytest.mark.parametrize("policy", POLICIES.values(), ids=lambda p: p.policy_id)
def test_decide_agrees_with_the_contract(policy):
    for states in itertools.product(CheckState, repeat=len(policy.checks)):
        checks = dict(zip(policy.checks, states))
        decision = policy.decide(checks)
        hard_ok = all(policy.is_closed(c, checks[c]) for c in policy.required_checks)
        n_a_ok = all(
            s is not CheckState.NOT_APPLICABLE or c in policy.not_applicable_allowed
            for c, s in checks.items()
        )
        assert decision.reason_codes or decision.status is ResultStatus.READY
        assert set(decision.reason_codes) <= ALLOWED_REASONS[decision.status]
        if decision.status in (ResultStatus.READY, ResultStatus.NEEDS_REVIEW):
            assert hard_ok, checks
        else:
            assert not hard_ok, checks
        if not n_a_ok:
            # decide() treats a disallowed not_applicable as unknown; the
            # contract rejects it outright, so no result can be built.
            continue
        if decision.status is ResultStatus.READY:
            _ready(policy, checks, CompletionBasis.AUTOMATED_CHECKS)
        elif decision.status is ResultStatus.NEEDS_REVIEW:
            pending = _needs_review(policy, checks, decision.reason_codes)
            accepted = accept_review(pending, decision.advisory_open, artifact_id="artifact_1")
            assert accepted.checks == pending.checks
            assert accepted.completion_basis is CompletionBasis.HUMAN_REVIEW


def test_required_unknown_cannot_be_ready_even_with_review():
    data = example("result-ready")
    data["checks"]["extracted_text"] = "unknown"
    with expect_error(ErrorCode.INVALID_RESULT, "checks"):
        result_from_dict(data)
    data.update(completion_basis="human_review", review={"accepted_checks": ["extracted_text"]})
    with expect_error(ErrorCode.INVALID_RESULT, "checks"):
        result_from_dict(data)


def test_human_review_cannot_override_a_size_failure():
    data = example("result-ready-human-review")
    data["output_bytes"] = data["target_bytes"] + 1
    data["checks"]["size_ceiling"] = "fail"
    data["review"] = {"accepted_checks": ["size_ceiling", "visual_policy"]}
    with expect_error(ErrorCode.INVALID_RESULT):
        result_from_dict(data)
    # A passing size check cannot hide an output over the target either.
    data["checks"]["size_ceiling"] = "pass"
    data["review"] = {"accepted_checks": ["visual_policy"]}
    with expect_error(ErrorCode.INVALID_RESULT, "checks.size_ceiling"):
        result_from_dict(data)


def test_human_review_cannot_override_a_preservation_failure():
    pending = result_from_dict(example("result-needs-review"))
    with expect_error(ErrorCode.INVALID_RESULT, "review.accepted_checks"):
        accept_review(
            pending, [CheckId.VISUAL_POLICY, CheckId.EXTRACTED_TEXT], artifact_id="artifact_1"
        )
    failing = {**pending.checks, CheckId.EXTRACTED_TEXT: CheckState.FAIL}
    with expect_error(ErrorCode.INVALID_RESULT, "checks"):
        _needs_review(POLICIES["preserve-existing-text-v1"], failing, pending.reason_codes)


def test_review_must_name_exactly_the_open_advisory_checks():
    pending = result_from_dict(example("result-needs-review"))
    with expect_error(ErrorCode.INVALID_RESULT, "review.accepted_checks"):
        accept_review(pending, [], artifact_id="artifact_1")
    accepted = accept_review(pending, [CheckId.VISUAL_POLICY], artifact_id="artifact_1")
    assert accepted.status is ResultStatus.READY
    assert accepted.checks[CheckId.VISUAL_POLICY] is CheckState.UNKNOWN
    assert accepted.review == ReviewDecision((CheckId.VISUAL_POLICY,))
    assert accepted.reason_codes == ()


def test_human_review_needs_something_to_accept():
    data = example("result-ready")
    data.update(completion_basis="human_review", review={"accepted_checks": ["visual_policy"]})
    with expect_error(ErrorCode.INVALID_RESULT):
        result_from_dict(data)


@pytest.mark.parametrize(
    ("expired", "reason", "action"),
    [
        (False, ReasonCode.REVIEW_REJECTED, NextAction.NONE),
        (True, ReasonCode.REVIEW_EXPIRED, NextAction.RETRY),
    ],
)
def test_rejected_or_expired_review_cancels(expired, reason, action):
    pending = result_from_dict(example("result-needs-review"))
    cancelled = reject_review(pending, expired=expired)
    assert cancelled.status is ResultStatus.CANCELLED
    assert cancelled.reason_codes == (reason,)
    assert cancelled.next_action is action
    assert cancelled.artifact_id is None


def test_only_needs_review_results_can_be_resolved():
    ready = result_from_dict(example("result-ready"))
    with expect_error(ErrorCode.INVALID_RESULT, "status"):
        accept_review(ready, [CheckId.VISUAL_POLICY], artifact_id="artifact_2")
    with expect_error(ErrorCode.INVALID_RESULT, "status"):
        reject_review(ready)


def test_needs_review_reasons_must_match_open_checks():
    data = example("result-needs-review")
    data["reason_codes"] = ["VALIDATION_INCONCLUSIVE"]
    with expect_error(ErrorCode.INVALID_RESULT, "reason_codes"):
        result_from_dict(data)


def test_needs_review_requires_an_open_advisory_check():
    data = example("result-needs-review")
    data["checks"]["visual_policy"] = "pass"
    with expect_error(ErrorCode.INVALID_RESULT, "checks"):
        result_from_dict(data)


# --- Structural result rules ---------------------------------------------------------


def mutate(name: str, **changes) -> dict:
    data = copy.deepcopy(example(name))
    for key, value in changes.items():
        if value is ...:
            data.pop(key, None)
        else:
            data[key] = value
    return data


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (mutate("result-ready", artifact_id=...), "artifact_id"),
        (mutate("result-ready", output_bytes=...), "output_bytes"),
        (mutate("result-ready", completion_basis=...), "completion_basis"),
        (mutate("result-ready", reason_codes=["QUALITY_REVIEW_REQUIRED"]), "reason_codes"),
        (mutate("result-ready", output_bytes=2_000_001), "checks.size_ceiling"),
        (mutate("result-ready", output_bytes=0), "output_bytes"),
        (mutate("result-ready", next_action="retry"), "next_action"),
        (mutate("result-ready", checks={"size_ceiling": "pass"}), "checks"),
        (mutate("result-ready", review={"accepted_checks": ["visual_policy"]}), "review"),
        (mutate("result-ready-unchanged-source", search={"attempts": 3, "max_attempts": 16}), "search.attempts"),
        (mutate("result-needs-review", artifact_id="artifact_x"), "artifact_id"),
        (mutate("result-needs-review", completion_basis="human_review"), "completion_basis"),
        (mutate("result-needs-review", output_bytes=...), "output_bytes"),
        (mutate("result-target-not-met", search=...), "search"),
        (
            mutate("result-target-not-met", search={"attempts": 4, "max_attempts": 16, "smallest_tested_bytes": 500_000}),
            "search.smallest_tested_bytes",
        ),
        (mutate("result-target-not-met", output_bytes=731_884), "output_bytes"),
        (mutate("result-target-not-met", reason_codes=["TARGET_NOT_MET", "DEADLINE_EXCEEDED"]), "reason_codes"),
        (mutate("result-blocked", reason_codes=[]), "reason_codes"),
        (mutate("result-blocked", reason_codes=["SIGNED_INPUT", "SIGNED_INPUT"]), "reason_codes"),
        (mutate("result-blocked", reason_codes=["TARGET_NOT_MET"]), "reason_codes"),
        (mutate("result-blocked", checks={"feature_support": "pass"}), "checks.feature_support"),
        (mutate("result-blocked", reason_codes=["PRESERVATION_CONSTRAINT_FAILED"]), "checks"),
        (mutate("result-blocked", reason_codes=["TEXT_LAYER_MISSING"]), "reason_codes"),
        (mutate("result-blocked", checks={"text_layer": "fail"}), "checks.text_layer"),
        (mutate("result-blocked", checks={"page_structure": "not_applicable"}), "checks.page_structure"),
        (mutate("result-blocked", checks={"bogus": "pass"}), "checks"),
        (mutate("result-blocked", checks={"page_structure": "maybe"}), "checks.page_structure"),
        (mutate("result-failed", reason_codes=["CANCELLED_BY_USER"]), "reason_codes"),
        (mutate("result-cancelled", reason_codes=["CANCELLED_BY_USER", "DEADLINE_EXCEEDED"]), "reason_codes"),
        (mutate("result-cancelled", search={"attempts": 17, "max_attempts": 16}), "search.attempts"),
        (mutate("result-cancelled", job_id="../job"), "job_id"),
        (mutate("result-cancelled", limitations=["x" * 501]), "limitations"),
        (mutate("result-cancelled", status="paused"), "status"),
    ],
)
def test_invalid_results_are_rejected(data, field):
    with expect_error(ErrorCode.INVALID_RESULT, field):
        result_from_dict(data)


def test_result_rejects_unknown_fields():
    with expect_error(ErrorCode.INVALID_RESULT):
        result_from_dict({**example("result-ready"), "source_path": "/home/user/statement.pdf"})


def test_validate_result_accepts_directly_built_results():
    result = build_result(
        job_id="job_1",
        file_id="file_1",
        status=ResultStatus.TARGET_NOT_MET,
        reason_codes=(ReasonCode.TARGET_NOT_MET,),
        policy_id="preserve-existing-text-v1",
        target_bytes=100,
        search=SearchSummary(attempts=2, max_attempts=16, smallest_tested_bytes=101),
    )
    assert result.next_action is NextAction.INCREASE_TARGET
    assert validate_result(result).policy_id == "preserve-existing-text-v1"


# --- Next action -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "reasons", "action"),
    [
        (ResultStatus.READY, [], NextAction.USE_LOCAL_ARTIFACT),
        (ResultStatus.NEEDS_REVIEW, [ReasonCode.QUALITY_REVIEW_REQUIRED], NextAction.OPEN_LOCAL_REVIEW),
        (ResultStatus.TARGET_NOT_MET, [ReasonCode.TARGET_NOT_MET], NextAction.INCREASE_TARGET),
        (ResultStatus.BLOCKED, [ReasonCode.ENCRYPTED_INPUT], NextAction.HANDLE_MANUALLY),
        (ResultStatus.BLOCKED, [ReasonCode.UNSUPPORTED_FEATURE, ReasonCode.ACCESS_DENIED], NextAction.REQUEST_ACCESS),
        (ResultStatus.BLOCKED, [ReasonCode.SOURCE_CHANGED], NextAction.RETRY),
        (ResultStatus.BLOCKED, [ReasonCode.OUTPUT_CONFLICT], NextAction.RESOLVE_OUTPUT_CONFLICT),
        (ResultStatus.FAILED, [ReasonCode.BACKEND_UNAVAILABLE], NextAction.CHECK_INSTALLATION),
        (ResultStatus.FAILED, [ReasonCode.INTERNAL_ERROR], NextAction.REPORT_FAILURE),
        (ResultStatus.CANCELLED, [ReasonCode.CANCELLED_BY_USER], NextAction.NONE),
        (ResultStatus.CANCELLED, [ReasonCode.DEADLINE_EXCEEDED], NextAction.RETRY),
    ],
)
def test_next_action_is_derived_from_status_and_reasons(status, reasons, action):
    assert next_action_for(status, reasons) is action
