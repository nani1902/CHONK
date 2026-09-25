"""Serialization and semantic validation for the versioned contract (schema 1.0).

Everything that crosses a machine boundary goes through this module:

* :func:`request_from_dict` / :func:`request_to_dict` for :class:`PrepareRequest`
* :func:`result_from_dict` / :func:`result_to_dict` for :class:`PreparationResult`
* :func:`error_to_dict` for a rejected payload

Parsing is strict: unknown fields, unknown enum values, unsupported schema or
policy versions, booleans or floats in integer fields, and out-of-range numbers
are rejected. :func:`validate_result` enforces the rules that JSON Schema
cannot express, most importantly that a ``ready`` or ``needs_review`` result has
no required check in ``unknown`` or ``fail``, and that human review accepts
only the advisory checks that are actually open.

:func:`json_schemas` returns structural JSON Schemas for external clients. They
are generated from the same enums and limits, and checked into ``schemas/``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from enum import Enum
from typing import Any, Iterable, Mapping, TypeVar

from chonk.models import (
    MAX_ATTEMPTS,
    MAX_DEADLINE_SECONDS,
    MAX_TARGET_BYTES,
    MIN_ATTEMPTS,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    CheckId,
    CheckState,
    CompletionBasis,
    ContractError,
    ErrorCode,
    NextAction,
    PreparationResult,
    PrepareRequest,
    ReasonCode,
    ResultStatus,
    ReviewDecision,
    SearchSummary,
)
from chonk.policies import POLICIES, ImageOnlyPages, PolicyDefinition, get_policy

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
MAX_OUTPUT_BYTES = 2**53 - 1
MAX_LIMITATIONS = 16
MAX_LIMITATION_LENGTH = 500

_ID = re.compile(ID_PATTERN)
_IDEMPOTENCY_KEY = re.compile(IDEMPOTENCY_KEY_PATTERN)

# --- Status and reason rules -------------------------------------------------

ALLOWED_REASONS: Mapping[ResultStatus, frozenset[ReasonCode]] = {
    ResultStatus.READY: frozenset(),
    ResultStatus.NEEDS_REVIEW: frozenset(
        {ReasonCode.QUALITY_REVIEW_REQUIRED, ReasonCode.VALIDATION_INCONCLUSIVE}
    ),
    ResultStatus.TARGET_NOT_MET: frozenset({ReasonCode.TARGET_NOT_MET}),
    ResultStatus.BLOCKED: frozenset(
        {
            ReasonCode.ENCRYPTED_INPUT,
            ReasonCode.SIGNED_INPUT,
            ReasonCode.UNSUPPORTED_FEATURE,
            ReasonCode.INSPECTION_INCONCLUSIVE,
            ReasonCode.MALFORMED_INPUT,
            ReasonCode.TEXT_LAYER_MISSING,
            ReasonCode.SOURCE_CHANGED,
            ReasonCode.ACCESS_DENIED,
            ReasonCode.OUTPUT_CONFLICT,
            ReasonCode.PRESERVATION_CONSTRAINT_FAILED,
            ReasonCode.VALIDATION_INCONCLUSIVE,
            ReasonCode.RESOURCE_LIMIT_EXCEEDED,
        }
    ),
    ResultStatus.FAILED: frozenset(
        {
            ReasonCode.BACKEND_UNAVAILABLE,
            ReasonCode.BACKEND_FAILED,
            ReasonCode.INTERNAL_ERROR,
            ReasonCode.RESOURCE_LIMIT_EXCEEDED,
        }
    ),
    ResultStatus.CANCELLED: frozenset(
        {
            ReasonCode.CANCELLED_BY_USER,
            ReasonCode.DEADLINE_EXCEEDED,
            ReasonCode.REVIEW_REJECTED,
            ReasonCode.REVIEW_EXPIRED,
        }
    ),
}
_SINGLE_REASON = {ResultStatus.TARGET_NOT_MET, ResultStatus.CANCELLED}
_FEATURE_REASONS = {
    ReasonCode.ENCRYPTED_INPUT,
    ReasonCode.SIGNED_INPUT,
    ReasonCode.UNSUPPORTED_FEATURE,
    ReasonCode.INSPECTION_INCONCLUSIVE,
}

_REASON_ACTIONS: Mapping[ReasonCode, NextAction] = {
    ReasonCode.ACCESS_DENIED: NextAction.REQUEST_ACCESS,
    ReasonCode.OUTPUT_CONFLICT: NextAction.RESOLVE_OUTPUT_CONFLICT,
    ReasonCode.SOURCE_CHANGED: NextAction.RETRY,
    ReasonCode.DEADLINE_EXCEEDED: NextAction.RETRY,
    ReasonCode.REVIEW_EXPIRED: NextAction.RETRY,
    ReasonCode.BACKEND_UNAVAILABLE: NextAction.CHECK_INSTALLATION,
    ReasonCode.BACKEND_FAILED: NextAction.REPORT_FAILURE,
    ReasonCode.INTERNAL_ERROR: NextAction.REPORT_FAILURE,
    ReasonCode.CANCELLED_BY_USER: NextAction.NONE,
    ReasonCode.REVIEW_REJECTED: NextAction.NONE,
}
_ACTION_PRECEDENCE = (
    NextAction.REQUEST_ACCESS,
    NextAction.RESOLVE_OUTPUT_CONFLICT,
    NextAction.RETRY,
    NextAction.CHECK_INSTALLATION,
    NextAction.HANDLE_MANUALLY,
    NextAction.REPORT_FAILURE,
    NextAction.NONE,
)


def next_action_for(status: ResultStatus, reason_codes: Iterable[ReasonCode]) -> NextAction:
    """The single next action implied by a status and its reasons.

    With several reasons, the action a caller can take soonest wins (for
    example, requesting access before handling the file manually).
    """
    if status is ResultStatus.READY:
        return NextAction.USE_LOCAL_ARTIFACT
    if status is ResultStatus.NEEDS_REVIEW:
        return NextAction.OPEN_LOCAL_REVIEW
    if status is ResultStatus.TARGET_NOT_MET:
        return NextAction.INCREASE_TARGET
    actions = {_REASON_ACTIONS.get(reason, NextAction.HANDLE_MANUALLY) for reason in reason_codes}
    for action in _ACTION_PRECEDENCE:
        if action in actions:
            return action
    return NextAction.NONE


# --- Primitive parsing helpers ------------------------------------------------

E = TypeVar("E", bound=Enum)


def _fail(code: ErrorCode, message: str, field: str | None) -> ContractError:
    return ContractError(code, message, field=field)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int(value: object, field: str, low: int, high: int, code: ErrorCode) -> int:
    if not _is_int(value):
        raise _fail(code, "must be an integer.", field)
    if not low <= value <= high:
        raise _fail(code, f"must be between {low:,} and {high:,}.", field)
    return value


def _identifier(value: object, field: str, code: ErrorCode, pattern=_ID) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _fail(
            code,
            "must be 1-128 characters: letters, digits, and the permitted separators.",
            field,
        )
    return value


def _enum(enum: type[E], value: object, field: str, code: ErrorCode) -> E:
    if isinstance(value, enum):
        return value
    if isinstance(value, str):
        try:
            return enum(value)
        except ValueError:
            pass
    raise _fail(code, f"must be one of: {', '.join(m.value for m in enum)}.", field)


def _schema_version(value: object, code_if_missing: ErrorCode) -> str:
    if value is None:
        raise _fail(code_if_missing, "is required.", "schema_version")
    if not isinstance(value, str):
        raise _fail(code_if_missing, "must be a string.", "schema_version")
    if value not in SUPPORTED_SCHEMA_VERSIONS:
        raise _fail(
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            f"Unsupported schema version. Supported: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))}.",
            "schema_version",
        )
    return value


def _object(data: object, allowed: set[str], required: set[str], code: ErrorCode, where: str | None):
    if not isinstance(data, Mapping):
        raise _fail(code, "must be a JSON object.", where)
    prefix = f"{where}." if where else ""
    for key in data:
        if not isinstance(key, str) or key not in allowed:
            # The unknown name is caller-supplied, so it is not echoed back.
            raise _fail(
                code, f"contains an unrecognized field; allowed: {', '.join(sorted(allowed))}.", where
            )
    for key in sorted(required):
        if data.get(key) is None:
            raise _fail(code, "is required.", f"{prefix}{key}")
    return data


# --- Requests -----------------------------------------------------------------

_REQUEST_FIELDS = {
    "schema_version",
    "file_id",
    "target_bytes",
    "policy_id",
    "deadline_seconds",
    "max_attempts",
    "idempotency_key",
}
_REQUEST_REQUIRED = _REQUEST_FIELDS - {"max_attempts", "idempotency_key"}


def validate_request(request: PrepareRequest) -> PolicyDefinition:
    """Raise :class:`ContractError` unless ``request`` is valid; return its policy."""
    code = ErrorCode.INVALID_REQUEST
    _schema_version(request.schema_version, code)
    _identifier(request.file_id, "file_id", code)
    _int(request.target_bytes, "target_bytes", 1, MAX_TARGET_BYTES, code)
    policy = get_policy(request.policy_id)
    _int(request.deadline_seconds, "deadline_seconds", 1, MAX_DEADLINE_SECONDS, code)
    _int(request.max_attempts, "max_attempts", MIN_ATTEMPTS, MAX_ATTEMPTS, code)
    if request.idempotency_key is not None:
        _identifier(request.idempotency_key, "idempotency_key", code, _IDEMPOTENCY_KEY)
    return policy


def request_from_dict(data: object) -> PrepareRequest:
    """Parse and validate a decoded JSON request.

    The schema version is checked before anything else, so a request written
    for a newer schema is reported as such rather than as unknown fields.
    """
    code = ErrorCode.INVALID_REQUEST
    if not isinstance(data, Mapping):
        raise _fail(code, "must be a JSON object.", None)
    _schema_version(data.get("schema_version"), code)
    _object(data, _REQUEST_FIELDS, _REQUEST_REQUIRED, code, None)
    optional = {
        key: data[key]
        for key in ("max_attempts", "idempotency_key")
        if data.get(key) is not None
    }
    request = PrepareRequest(
        schema_version=data["schema_version"],
        file_id=data["file_id"],
        target_bytes=data["target_bytes"],
        policy_id=data["policy_id"],
        deadline_seconds=data["deadline_seconds"],
        **optional,
    )
    validate_request(request)
    return request


def request_to_dict(request: PrepareRequest) -> dict[str, Any]:
    validate_request(request)
    data: dict[str, Any] = {
        "schema_version": request.schema_version,
        "file_id": request.file_id,
        "target_bytes": request.target_bytes,
        "policy_id": request.policy_id,
        "deadline_seconds": request.deadline_seconds,
        "max_attempts": request.max_attempts,
    }
    if request.idempotency_key is not None:
        data["idempotency_key"] = request.idempotency_key
    return data


# --- Results ------------------------------------------------------------------

def _invalid(message: str, field: str | None = None) -> ContractError:
    return ContractError(ErrorCode.INVALID_RESULT, message, field=field)


def validate_result(result: PreparationResult) -> PolicyDefinition:
    """Raise :class:`ContractError` unless ``result`` satisfies the contract.

    Returns the result's policy definition.
    """
    code = ErrorCode.INVALID_RESULT
    _schema_version(result.schema_version, code)
    _identifier(result.job_id, "job_id", code)
    _identifier(result.file_id, "file_id", code)
    policy = get_policy(result.policy_id)
    _int(result.target_bytes, "target_bytes", 1, MAX_TARGET_BYTES, code)
    status = _enum(ResultStatus, result.status, "status", code)
    next_action = _enum(NextAction, result.next_action, "next_action", code)
    ready = status is ResultStatus.READY

    reasons = tuple(_enum(ReasonCode, r, "reason_codes", code) for r in result.reason_codes)
    if len(set(reasons)) != len(reasons):
        raise _invalid("must not repeat a reason code.", "reason_codes")
    disallowed = [r.value for r in reasons if r not in ALLOWED_REASONS[status]]
    if disallowed:
        raise _invalid(f"not allowed for status {status.value}: {', '.join(disallowed)}.", "reason_codes")
    if ready and reasons:
        raise _invalid("a ready result has no reason codes.", "reason_codes")
    if not ready and not reasons:
        raise _invalid(f"status {status.value} requires a reason code.", "reason_codes")
    if status in _SINGLE_REASON and len(reasons) != 1:
        raise _invalid(f"status {status.value} takes exactly one reason code.", "reason_codes")

    checks = _validate_checks(result.checks, policy)

    if result.output_bytes is not None:
        _int(result.output_bytes, "output_bytes", 1, MAX_OUTPUT_BYTES, code)
        size = checks.get(CheckId.SIZE_CEILING)
        fits = result.output_bytes <= result.target_bytes
        if size is CheckState.PASS and not fits:
            raise _invalid("size_ceiling passed but output_bytes exceeds target_bytes.", "checks.size_ceiling")
        if size is CheckState.FAIL and fits:
            raise _invalid("size_ceiling failed but output_bytes is within target_bytes.", "checks.size_ceiling")

    if result.search is not None:
        _validate_search(result.search, result.target_bytes, status)
    if result.artifact_id is not None:
        _identifier(result.artifact_id, "artifact_id", code)
    if (result.artifact_id is not None) != ready:
        raise _invalid("an artifact_id is present exactly when the status is ready.", "artifact_id")
    if (result.completion_basis is not None) != ready:
        raise _invalid("a completion_basis is present exactly when the status is ready.", "completion_basis")
    if result.review is not None and not ready:
        raise _invalid("only a ready result records a review decision.", "review")

    if ready:
        _validate_ready(result, policy, checks)
    elif status is ResultStatus.NEEDS_REVIEW:
        _validate_needs_review(result, policy, checks, reasons)
    elif status is ResultStatus.TARGET_NOT_MET:
        if result.search is None or result.search.smallest_tested_bytes is None:
            raise _invalid("target_not_met requires search evidence with the smallest tested size.", "search")
        if result.search.attempts < 1:
            raise _invalid("target_not_met requires at least one tested candidate.", "search.attempts")
        if result.output_bytes is not None:
            raise _invalid("target_not_met has no output.", "output_bytes")
        if checks.get(CheckId.SIZE_CEILING, CheckState.FAIL) is not CheckState.FAIL:
            raise _invalid("target_not_met requires size_ceiling to fail if reported.", "checks.size_ceiling")
    elif status is ResultStatus.BLOCKED:
        _validate_blocked(policy, checks, reasons)

    _validate_limitations(result.limitations)

    expected = next_action_for(status, reasons)
    if next_action is not expected:
        raise _invalid(f"must be {expected.value} for this status and reasons.", "next_action")
    return policy


def _validate_checks(checks: object, policy: PolicyDefinition) -> dict[CheckId, CheckState]:
    code = ErrorCode.INVALID_RESULT
    if not isinstance(checks, Mapping):
        raise _invalid("must be an object.", "checks")
    parsed: dict[CheckId, CheckState] = {}
    for key, value in checks.items():
        check = _enum(CheckId, key, "checks", code)
        field = f"checks.{check.value}"
        if check not in policy.checks:
            raise _invalid(f"is not a check of policy {policy.policy_id}.", field)
        state = _enum(CheckState, value, field, code)
        if state is CheckState.NOT_APPLICABLE and check not in policy.not_applicable_allowed:
            raise _invalid(f"cannot be not_applicable under {policy.policy_id}.", field)
        parsed[check] = state
    return parsed


def _require_complete(policy: PolicyDefinition, checks: Mapping[CheckId, CheckState], status: str) -> None:
    missing = [c.value for c in policy.checks if c not in checks]
    if missing:
        raise _invalid(f"a {status} result reports every policy check; missing: {', '.join(missing)}.", "checks")


def _hard_violations(policy: PolicyDefinition, checks: Mapping[CheckId, CheckState]) -> list[str]:
    return [
        f"{c.value}={checks[c].value}"
        for c in policy.required_checks
        if not policy.is_closed(c, checks[c])
    ]


def _validate_ready(result: PreparationResult, policy: PolicyDefinition, checks) -> None:
    code = ErrorCode.INVALID_RESULT
    basis = _enum(CompletionBasis, result.completion_basis, "completion_basis", code)
    _require_complete(policy, checks, "ready")
    if result.output_bytes is None:
        raise _invalid("a ready result reports output_bytes.", "output_bytes")

    violations = _hard_violations(policy, checks)
    if violations:
        # A required unknown or failure can never be ready, whatever the basis.
        raise _invalid(
            f"required checks must pass for a ready result: {', '.join(violations)}.", "checks"
        )
    decision = policy.decide(checks)

    if basis is CompletionBasis.HUMAN_REVIEW:
        if result.review is None:
            raise _invalid("a human_review result records the review decision.", "review")
        accepted = tuple(
            _enum(CheckId, c, "review.accepted_checks", code) for c in result.review.accepted_checks
        )
        if len(set(accepted)) != len(accepted):
            raise _invalid("must not repeat a check.", "review.accepted_checks")
        not_advisory = [c.value for c in accepted if c not in policy.advisory_checks]
        if not_advisory:
            raise _invalid(
                f"a person can accept only advisory checks; not advisory: {', '.join(not_advisory)}.",
                "review.accepted_checks",
            )
        if not decision.advisory_open:
            raise _invalid("human_review requires at least one open advisory check.", "completion_basis")
        if set(accepted) != set(decision.advisory_open):
            raise _invalid(
                "must name exactly the open advisory checks: "
                f"{', '.join(c.value for c in decision.advisory_open)}.",
                "review.accepted_checks",
            )
    else:
        if result.review is not None:
            raise _invalid("only a human_review result records a review decision.", "review")
        if decision.status is not ResultStatus.READY:
            open_checks = ", ".join(c.value for c in decision.advisory_open)
            raise _invalid(
                f"{basis.value} requires every advisory check to pass; open: {open_checks}.", "checks"
            )
        if basis is CompletionBasis.UNCHANGED_SOURCE and result.search is not None and result.search.attempts:
            raise _invalid("an unchanged source was not produced by a search.", "search.attempts")


def _validate_needs_review(result, policy: PolicyDefinition, checks, reasons) -> None:
    _require_complete(policy, checks, "needs_review")
    if result.output_bytes is None:
        raise _invalid("a needs_review result reports output_bytes.", "output_bytes")
    violations = _hard_violations(policy, checks)
    if violations:
        raise _invalid(
            f"review cannot resolve a required check: {', '.join(violations)}.", "checks"
        )
    decision = policy.decide(checks)
    if decision.status is not ResultStatus.NEEDS_REVIEW:
        raise _invalid("needs_review requires at least one open advisory check.", "checks")
    if set(reasons) != set(decision.reason_codes):
        expected = ", ".join(r.value for r in decision.reason_codes)
        raise _invalid(f"must match the open advisory checks: {expected}.", "reason_codes")


def _validate_blocked(policy: PolicyDefinition, checks, reasons) -> None:
    if ReasonCode.PRESERVATION_CONSTRAINT_FAILED in reasons:
        preservation = [
            c
            for c in policy.required_checks
            if c not in (CheckId.SIZE_CEILING, CheckId.FEATURE_SUPPORT)
            and checks.get(c) is CheckState.FAIL
        ]
        if not preservation:
            raise _invalid(
                "PRESERVATION_CONSTRAINT_FAILED requires a failed required preservation check.",
                "checks",
            )
    if ReasonCode.TEXT_LAYER_MISSING in reasons and policy.image_only_pages is not ImageOnlyPages.BLOCK:
        raise _invalid(f"{policy.policy_id} does not block pages without text.", "reason_codes")
    if any(r in _FEATURE_REASONS for r in reasons) and checks.get(CheckId.FEATURE_SUPPORT) is CheckState.PASS:
        raise _invalid("a feature reason contradicts a passing feature_support check.", "checks.feature_support")


def _validate_search(search: SearchSummary, target_bytes: int, status: ResultStatus) -> None:
    code = ErrorCode.INVALID_RESULT
    _int(search.max_attempts, "search.max_attempts", MIN_ATTEMPTS, MAX_ATTEMPTS, code)
    _int(search.attempts, "search.attempts", 0, search.max_attempts, code)
    if search.smallest_tested_bytes is not None:
        _int(search.smallest_tested_bytes, "search.smallest_tested_bytes", 1, MAX_OUTPUT_BYTES, code)
        if search.attempts < 1:
            raise _invalid("a smallest tested size needs at least one attempt.", "search.smallest_tested_bytes")
        if status is ResultStatus.TARGET_NOT_MET and search.smallest_tested_bytes <= target_bytes:
            raise _invalid(
                "target_not_met contradicts a tested candidate within the target.",
                "search.smallest_tested_bytes",
            )


def _validate_limitations(limitations: object) -> None:
    if not isinstance(limitations, (tuple, list)) or len(limitations) > MAX_LIMITATIONS:
        raise _invalid(f"must be a list of at most {MAX_LIMITATIONS} strings.", "limitations")
    for item in limitations:
        if not isinstance(item, str) or not 1 <= len(item) <= MAX_LIMITATION_LENGTH:
            raise _invalid(f"each entry must be 1-{MAX_LIMITATION_LENGTH} characters.", "limitations")


_RESULT_FIELDS = {
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
_RESULT_REQUIRED = {
    "schema_version",
    "job_id",
    "file_id",
    "status",
    "reason_codes",
    "policy_id",
    "target_bytes",
    "checks",
    "limitations",
    "next_action",
}


def result_from_dict(data: object) -> PreparationResult:
    """Parse and validate a decoded JSON result."""
    code = ErrorCode.INVALID_RESULT
    if not isinstance(data, Mapping):
        raise _fail(code, "must be a JSON object.", None)
    _schema_version(data.get("schema_version"), code)
    _object(data, _RESULT_FIELDS, _RESULT_REQUIRED, code, None)

    reasons = data["reason_codes"]
    if not isinstance(reasons, list):
        raise _invalid("must be a list.", "reason_codes")
    checks = data["checks"]
    if not isinstance(checks, Mapping):
        raise _invalid("must be an object.", "checks")
    limitations = data["limitations"]
    if not isinstance(limitations, list):
        raise _invalid("must be a list.", "limitations")

    search = None
    if data.get("search") is not None:
        raw = _object(
            data["search"],
            {"attempts", "max_attempts", "smallest_tested_bytes"},
            {"attempts", "max_attempts"},
            code,
            "search",
        )
        search = SearchSummary(
            attempts=raw["attempts"],
            max_attempts=raw["max_attempts"],
            smallest_tested_bytes=raw.get("smallest_tested_bytes"),
        )
    review = None
    if data.get("review") is not None:
        raw = _object(data["review"], {"accepted_checks"}, {"accepted_checks"}, code, "review")
        if not isinstance(raw["accepted_checks"], list):
            raise _invalid("must be a list.", "review.accepted_checks")
        review = ReviewDecision(
            accepted_checks=tuple(
                _enum(CheckId, c, "review.accepted_checks", code) for c in raw["accepted_checks"]
            )
        )

    basis = data.get("completion_basis")
    result = PreparationResult(
        schema_version=data["schema_version"],
        job_id=data["job_id"],
        file_id=data["file_id"],
        status=_enum(ResultStatus, data["status"], "status", code),
        reason_codes=tuple(_enum(ReasonCode, r, "reason_codes", code) for r in reasons),
        completion_basis=None if basis is None else _enum(CompletionBasis, basis, "completion_basis", code),
        policy_id=data["policy_id"],
        target_bytes=data["target_bytes"],
        output_bytes=data.get("output_bytes"),
        artifact_id=data.get("artifact_id"),
        checks={
            _enum(CheckId, k, "checks", code): _enum(CheckState, v, f"checks.{k}", code)
            for k, v in checks.items()
        },
        search=search,
        review=review,
        limitations=tuple(limitations),
        next_action=_enum(NextAction, data["next_action"], "next_action", code),
    )
    validate_result(result)
    return result


def result_to_dict(result: PreparationResult) -> dict[str, Any]:
    """Serialize a result after validating it. Absent optional fields are omitted."""
    policy = validate_result(result)
    data: dict[str, Any] = {
        "schema_version": result.schema_version,
        "job_id": result.job_id,
        "file_id": result.file_id,
        "status": ResultStatus(result.status).value,
        "reason_codes": [ReasonCode(r).value for r in result.reason_codes],
    }
    if result.completion_basis is not None:
        data["completion_basis"] = CompletionBasis(result.completion_basis).value
    data["policy_id"] = result.policy_id
    data["target_bytes"] = result.target_bytes
    if result.output_bytes is not None:
        data["output_bytes"] = result.output_bytes
    if result.artifact_id is not None:
        data["artifact_id"] = result.artifact_id
    checks = {CheckId(k): CheckState(v) for k, v in result.checks.items()}
    data["checks"] = {c.value: checks[c].value for c in policy.checks if c in checks}
    if result.search is not None:
        search: dict[str, Any] = {
            "attempts": result.search.attempts,
            "max_attempts": result.search.max_attempts,
        }
        if result.search.smallest_tested_bytes is not None:
            search["smallest_tested_bytes"] = result.search.smallest_tested_bytes
        data["search"] = search
    if result.review is not None:
        data["review"] = {"accepted_checks": [CheckId(c).value for c in result.review.accepted_checks]}
    data["limitations"] = list(result.limitations)
    data["next_action"] = NextAction(result.next_action).value
    return data


def build_result(**fields: Any) -> PreparationResult:
    """Construct a validated result, deriving ``next_action`` if omitted."""
    if "next_action" not in fields:
        status = ResultStatus(fields["status"])
        fields["next_action"] = next_action_for(status, fields.get("reason_codes", ()))
    result = PreparationResult(**fields)
    validate_result(result)
    return result


def accept_review(
    result: PreparationResult, accepted_checks: Iterable[CheckId], *, artifact_id: str
) -> PreparationResult:
    """Resolve a ``needs_review`` result that a person accepted.

    Check states are preserved as they were; the acceptance is recorded
    separately. Raises :class:`ContractError` if the acceptance names anything
    other than exactly the open advisory checks, so a hard failure can never be
    accepted.
    """
    validate_result(result)
    if ResultStatus(result.status) is not ResultStatus.NEEDS_REVIEW:
        raise _invalid("only a needs_review result can be accepted.", "status")
    accepted = replace(
        result,
        status=ResultStatus.READY,
        reason_codes=(),
        completion_basis=CompletionBasis.HUMAN_REVIEW,
        review=ReviewDecision(tuple(accepted_checks)),
        artifact_id=artifact_id,
        next_action=NextAction.USE_LOCAL_ARTIFACT,
    )
    validate_result(accepted)
    return accepted


def reject_review(result: PreparationResult, *, expired: bool = False) -> PreparationResult:
    """Cancel a ``needs_review`` result that a person rejected or let expire."""
    validate_result(result)
    if ResultStatus(result.status) is not ResultStatus.NEEDS_REVIEW:
        raise _invalid("only a needs_review result can be rejected.", "status")
    reason = ReasonCode.REVIEW_EXPIRED if expired else ReasonCode.REVIEW_REJECTED
    cancelled = replace(
        result,
        status=ResultStatus.CANCELLED,
        reason_codes=(reason,),
        next_action=next_action_for(ResultStatus.CANCELLED, (reason,)),
    )
    validate_result(cancelled)
    return cancelled


# --- Errors -------------------------------------------------------------------

def error_to_dict(error: ContractError) -> dict[str, Any]:
    body: dict[str, Any] = {"code": error.code.value, "message": error.message}
    if error.field is not None:
        body["field"] = error.field
    return {"schema_version": SCHEMA_VERSION, "error": body}


# --- JSON Schema ----------------------------------------------------------------

_SCHEMA_BASE = "https://github.com/nani1902/CHONK/schemas/" + SCHEMA_VERSION


def _values(enum: type[Enum]) -> list[str]:
    return [member.value for member in enum]


def json_schemas() -> dict[str, dict[str, Any]]:
    """Structural JSON Schemas (draft 2020-12) keyed by file stem.

    These describe shape, enums, and limits. Policy-dependent rules (which
    checks must pass for which status) are enforced by :func:`validate_result`.
    """
    version = {"type": "string", "const": SCHEMA_VERSION}
    identifier = {"type": "string", "pattern": ID_PATTERN}
    policy_id = {"type": "string", "enum": sorted(POLICIES)}

    request = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/prepare-request.schema.json",
        "title": "CHONK prepare request",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_REQUEST_REQUIRED),
        "properties": {
            "schema_version": version,
            "file_id": identifier,
            "target_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_TARGET_BYTES},
            "policy_id": policy_id,
            "deadline_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_DEADLINE_SECONDS},
            "max_attempts": {
                "type": "integer",
                "minimum": MIN_ATTEMPTS,
                "maximum": MAX_ATTEMPTS,
                "default": PrepareRequest.__dataclass_fields__["max_attempts"].default,
            },
            "idempotency_key": {"type": "string", "pattern": IDEMPOTENCY_KEY_PATTERN},
        },
    }

    def status_is(status: ResultStatus) -> dict[str, Any]:
        return {"properties": {"status": {"const": status.value}}, "required": ["status"]}

    reason_rules = [
        {
            "if": status_is(status),
            "then": {
                "properties": {
                    "reason_codes": (
                        {"maxItems": 0}
                        if not allowed
                        else {
                            "items": {"enum": sorted(r.value for r in allowed)},
                            "minItems": 1,
                            **({"maxItems": 1} if status in _SINGLE_REASON else {}),
                        }
                    )
                }
            },
        }
        for status, allowed in ALLOWED_REASONS.items()
    ]
    result = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/result.schema.json",
        "title": "CHONK preparation result",
        "description": (
            "Content-free result for one file. Structural schema only: CHONK "
            "additionally enforces policy rules, e.g. a ready or needs_review "
            "result never has a required check in unknown or fail."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_RESULT_REQUIRED),
        "properties": {
            "schema_version": version,
            "job_id": identifier,
            "file_id": identifier,
            "status": {"enum": _values(ResultStatus)},
            "reason_codes": {
                "type": "array",
                "uniqueItems": True,
                "items": {"enum": _values(ReasonCode)},
            },
            "completion_basis": {"enum": _values(CompletionBasis)},
            "policy_id": policy_id,
            "target_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_TARGET_BYTES},
            "output_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_OUTPUT_BYTES},
            "artifact_id": identifier,
            "checks": {
                "type": "object",
                "additionalProperties": False,
                "properties": {c.value: {"enum": _values(CheckState)} for c in CheckId},
            },
            "search": {
                "type": "object",
                "additionalProperties": False,
                "required": ["attempts", "max_attempts"],
                "properties": {
                    "attempts": {"type": "integer", "minimum": 0, "maximum": MAX_ATTEMPTS},
                    "max_attempts": {"type": "integer", "minimum": MIN_ATTEMPTS, "maximum": MAX_ATTEMPTS},
                    "smallest_tested_bytes": {"type": "integer", "minimum": 1, "maximum": MAX_OUTPUT_BYTES},
                },
            },
            "review": {
                "type": "object",
                "additionalProperties": False,
                "required": ["accepted_checks"],
                "properties": {
                    "accepted_checks": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"enum": _values(CheckId)},
                    }
                },
            },
            "limitations": {
                "type": "array",
                "maxItems": MAX_LIMITATIONS,
                "items": {"type": "string", "minLength": 1, "maxLength": MAX_LIMITATION_LENGTH},
            },
            "next_action": {"enum": _values(NextAction)},
        },
        "allOf": [
            {
                "if": status_is(ResultStatus.READY),
                "then": {"required": ["completion_basis", "artifact_id", "output_bytes"]},
                "else": {"not": {"anyOf": [{"required": ["completion_basis"]}, {"required": ["artifact_id"]}, {"required": ["review"]}]}},
            },
            {
                "if": {"properties": {"completion_basis": {"const": CompletionBasis.HUMAN_REVIEW.value}}, "required": ["completion_basis"]},
                "then": {"required": ["review"]},
                "else": {"not": {"required": ["review"]}},
            },
            {"if": status_is(ResultStatus.NEEDS_REVIEW), "then": {"required": ["output_bytes"]}},
            {"if": status_is(ResultStatus.TARGET_NOT_MET), "then": {"required": ["search"]}},
            *reason_rules,
        ],
    }

    error = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{_SCHEMA_BASE}/error.schema.json",
        "title": "CHONK contract error",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "error"],
        "properties": {
            "schema_version": version,
            "error": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message"],
                "properties": {
                    "code": {"enum": _values(ErrorCode)},
                    "message": {"type": "string"},
                    "field": {"type": "string"},
                },
            },
        },
    }
    return {"prepare-request": request, "result": result, "error": error}
