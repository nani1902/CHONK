"""Versioned preservation policies: which checks are hard, which advisory, and
which document features block processing.

A policy ID names an immutable definition, ``<name>-v<version>``. Changing what
a policy requires means adding a new version, never editing an existing one
after release. Agents select a policy explicitly and cannot weaken it.

The v1 definitions are provisional until the first release: preservation
validators (CHONK-008) may still refine them, and preflight inspection
(CHONK-005) added ``optional_content``. Until a feature has a validator, its
presence blocks transformation. ``docs/product/SUPPORTED_FEATURES.md``
documents the matrix, and a test keeps it in step with these definitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from chonk.models import CheckId, CheckState, ContractError, ErrorCode, ReasonCode, ResultStatus


class Feature(str, Enum):
    """Document features found by preflight inspection."""

    ENCRYPTION = "encryption"
    DIGITAL_SIGNATURE = "digital_signature"
    INTERACTIVE_FORM = "interactive_form"
    XFA_FORM = "xfa_form"
    ACTIVE_CONTENT = "active_content"
    EMBEDDED_FILES = "embedded_files"
    ANNOTATIONS = "annotations"
    LINKS = "links"
    OUTLINES = "outlines"
    TAGGED_STRUCTURE = "tagged_structure"
    OPTIONAL_CONTENT = "optional_content"
    """Layers (optional content groups). Hidden layers can hold content that
    the default view, and therefore visual comparison, does not show."""


class FeaturePresence(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    """Inspection failed or could not decide. Never treated as absent."""


class ImageOnlyPages(str, Enum):
    """How a policy treats content pages without an existing text layer
    (``image_only`` and ``graphics_only`` pages; blank pages are exempt).

    ``block`` is enforced at preflight through the ``text_layer`` check. Under
    ``review``, such pages never block; review of a rewritten output is decided
    by the preservation and visual validators.
    """

    REVIEW = "review"
    BLOCK = "block"


_FEATURE_REASONS = {
    Feature.ENCRYPTION: ReasonCode.ENCRYPTED_INPUT,
    Feature.DIGITAL_SIGNATURE: ReasonCode.SIGNED_INPUT,
}


@dataclass(frozen=True)
class FeatureEvaluation:
    check_state: CheckState
    reason_codes: tuple[ReasonCode, ...]
    blocking: tuple[Feature, ...]
    unknown: tuple[Feature, ...]


@dataclass(frozen=True)
class Decision:
    """What a complete set of checks allows under a policy, before any review."""

    status: ResultStatus
    reason_codes: tuple[ReasonCode, ...]
    advisory_open: tuple[CheckId, ...] = ()
    """Advisory checks a person would have to accept; only for ``NEEDS_REVIEW``."""


@dataclass(frozen=True)
class PolicyDefinition:
    name: str
    version: int
    summary: str
    required_checks: tuple[CheckId, ...]
    """Hard constraints. Must be ``pass`` (or an allowed ``not_applicable``) for
    any exportable or reviewable result; no person can override them."""
    advisory_checks: tuple[CheckId, ...]
    """Uncertainty a person may accept in local review."""
    not_applicable_allowed: frozenset[CheckId]
    blocked_features: frozenset[Feature]
    image_only_pages: ImageOnlyPages
    """``block`` requires ``text_layer`` as a hard check."""

    def __post_init__(self) -> None:
        overlap = set(self.required_checks) & set(self.advisory_checks)
        if overlap:
            raise ValueError(f"{self.policy_id}: checks both required and advisory: {overlap}")
        if CheckId.SIZE_CEILING not in self.required_checks:
            raise ValueError(f"{self.policy_id}: the size ceiling must be a required check")
        if CheckId.FEATURE_SUPPORT not in self.required_checks:
            raise ValueError(f"{self.policy_id}: feature support must be a required check")
        if not self.not_applicable_allowed <= set(self.checks):
            raise ValueError(f"{self.policy_id}: not_applicable_allowed names unknown checks")
        if self.image_only_pages is ImageOnlyPages.BLOCK and CheckId.TEXT_LAYER not in self.required_checks:
            raise ValueError(f"{self.policy_id}: blocking pages without text requires the text_layer check")

    @property
    def policy_id(self) -> str:
        return f"{self.name}-v{self.version}"

    @property
    def checks(self) -> tuple[CheckId, ...]:
        return self.required_checks + self.advisory_checks

    def is_required(self, check: CheckId) -> bool:
        return check in self.required_checks

    def is_closed(self, check: CheckId, state: CheckState) -> bool:
        """True if ``state`` satisfies ``check`` without review."""
        if state is CheckState.PASS:
            return True
        return state is CheckState.NOT_APPLICABLE and check in self.not_applicable_allowed

    def evaluate_features(
        self, inventory: Mapping[Feature, FeaturePresence]
    ) -> FeatureEvaluation:
        """Decide ``feature_support`` from a preflight inventory.

        A feature missing from ``inventory`` is treated as ``UNKNOWN``: failed or
        skipped inspection is never evidence of absence.
        """
        blocking: list[Feature] = []
        unknown: list[Feature] = []
        for feature in Feature:
            if feature not in self.blocked_features:
                continue
            presence = inventory.get(feature, FeaturePresence.UNKNOWN)
            if presence is FeaturePresence.PRESENT:
                blocking.append(feature)
            elif presence is not FeaturePresence.ABSENT:
                unknown.append(feature)

        reasons: list[ReasonCode] = []
        for feature in blocking:
            reason = _FEATURE_REASONS.get(feature, ReasonCode.UNSUPPORTED_FEATURE)
            if reason not in reasons:
                reasons.append(reason)
        if unknown:
            reasons.append(ReasonCode.INSPECTION_INCONCLUSIVE)

        if blocking:
            state = CheckState.FAIL
        elif unknown:
            state = CheckState.UNKNOWN
        else:
            state = CheckState.PASS
        return FeatureEvaluation(state, tuple(reasons), tuple(blocking), tuple(unknown))

    def decide(self, checks: Mapping[CheckId, CheckState]) -> Decision:
        """Classify a candidate's checks without human input.

        Precedence: a failed hard check other than size blocks; then a missed
        size ceiling; then an inconclusive hard check blocks; then open advisory
        checks need review; otherwise ready. A check absent from ``checks``, or
        ``not_applicable`` where the policy does not allow it, counts as
        ``unknown``.
        """
        states = {check: _effective_state(self, check, checks) for check in self.checks}

        hard_fail = [c for c in self.required_checks if states[c] is CheckState.FAIL]
        non_size_fail = [c for c in hard_fail if c is not CheckId.SIZE_CEILING]
        if non_size_fail:
            return Decision(ResultStatus.BLOCKED, _dedupe(_FAIL_REASONS[c] for c in non_size_fail))
        if hard_fail:
            return Decision(ResultStatus.TARGET_NOT_MET, (ReasonCode.TARGET_NOT_MET,))

        hard_unknown = [c for c in self.required_checks if states[c] is CheckState.UNKNOWN]
        if hard_unknown:
            return Decision(
                ResultStatus.BLOCKED, _dedupe(_UNKNOWN_REASONS[c] for c in hard_unknown)
            )

        advisory_open = tuple(c for c in self.advisory_checks if not self.is_closed(c, states[c]))
        if advisory_open:
            return Decision(
                ResultStatus.NEEDS_REVIEW,
                _dedupe(_advisory_reason(c, states[c]) for c in advisory_open),
                advisory_open,
            )
        return Decision(ResultStatus.READY, ())


def _effective_state(
    policy: PolicyDefinition, check: CheckId, checks: Mapping[CheckId, CheckState]
) -> CheckState:
    state = checks.get(check, CheckState.UNKNOWN)
    if state is CheckState.NOT_APPLICABLE and check not in policy.not_applicable_allowed:
        return CheckState.UNKNOWN
    return state


def _dedupe(reasons) -> tuple[ReasonCode, ...]:
    return tuple(dict.fromkeys(reasons))


_FAIL_REASONS = {
    CheckId.FEATURE_SUPPORT: ReasonCode.UNSUPPORTED_FEATURE,
    CheckId.PAGE_STRUCTURE: ReasonCode.PRESERVATION_CONSTRAINT_FAILED,
    CheckId.EXTRACTED_TEXT: ReasonCode.PRESERVATION_CONSTRAINT_FAILED,
    CheckId.TEXT_LAYER: ReasonCode.TEXT_LAYER_MISSING,
    CheckId.VISUAL_POLICY: ReasonCode.PRESERVATION_CONSTRAINT_FAILED,
}
_UNKNOWN_REASONS = {
    CheckId.SIZE_CEILING: ReasonCode.VALIDATION_INCONCLUSIVE,
    CheckId.FEATURE_SUPPORT: ReasonCode.INSPECTION_INCONCLUSIVE,
    CheckId.PAGE_STRUCTURE: ReasonCode.VALIDATION_INCONCLUSIVE,
    CheckId.EXTRACTED_TEXT: ReasonCode.VALIDATION_INCONCLUSIVE,
    CheckId.TEXT_LAYER: ReasonCode.VALIDATION_INCONCLUSIVE,
    CheckId.VISUAL_POLICY: ReasonCode.VALIDATION_INCONCLUSIVE,
}


def _advisory_reason(check: CheckId, state: CheckState) -> ReasonCode:
    if check is CheckId.VISUAL_POLICY or state is CheckState.FAIL:
        return ReasonCode.QUALITY_REVIEW_REQUIRED
    return ReasonCode.VALIDATION_INCONCLUSIVE


_V1_BLOCKED_FEATURES = frozenset(Feature)

PRESERVE_EXISTING_TEXT_V1 = PolicyDefinition(
    name="preserve-existing-text",
    version=1,
    summary=(
        "Ordinary supported PDFs. Existing text, page geometry and order, and "
        "supported features are hard constraints; visual uncertainty and "
        "image-only pages go to review."
    ),
    required_checks=(
        CheckId.SIZE_CEILING,
        CheckId.FEATURE_SUPPORT,
        CheckId.PAGE_STRUCTURE,
        CheckId.EXTRACTED_TEXT,
    ),
    advisory_checks=(CheckId.VISUAL_POLICY,),
    not_applicable_allowed=frozenset({CheckId.EXTRACTED_TEXT}),
    blocked_features=_V1_BLOCKED_FEATURES,
    image_only_pages=ImageOnlyPages.REVIEW,
)

REQUIRE_SEARCHABLE_V1 = PolicyDefinition(
    name="require-searchable",
    version=1,
    summary=(
        "Every relevant content page must already have a usable text layer, and "
        "it must be preserved. Scans without text are blocked; no OCR is added."
    ),
    required_checks=(
        CheckId.SIZE_CEILING,
        CheckId.FEATURE_SUPPORT,
        CheckId.PAGE_STRUCTURE,
        CheckId.TEXT_LAYER,
        CheckId.EXTRACTED_TEXT,
    ),
    advisory_checks=(CheckId.VISUAL_POLICY,),
    not_applicable_allowed=frozenset(),
    blocked_features=_V1_BLOCKED_FEATURES,
    image_only_pages=ImageOnlyPages.BLOCK,
)

SCAN_REVIEW_V1 = PolicyDefinition(
    name="scan-review",
    version=1,
    summary=(
        "Image-only and scanned PDFs. Structural checks are hard constraints; "
        "text preservation is not applicable where no layer exists and "
        "reviewable where one does; visual uncertainty goes to review."
    ),
    required_checks=(
        CheckId.SIZE_CEILING,
        CheckId.FEATURE_SUPPORT,
        CheckId.PAGE_STRUCTURE,
    ),
    advisory_checks=(CheckId.EXTRACTED_TEXT, CheckId.VISUAL_POLICY),
    not_applicable_allowed=frozenset({CheckId.EXTRACTED_TEXT}),
    blocked_features=_V1_BLOCKED_FEATURES,
    image_only_pages=ImageOnlyPages.REVIEW,
)

POLICIES: Mapping[str, PolicyDefinition] = {
    policy.policy_id: policy
    for policy in (PRESERVE_EXISTING_TEXT_V1, REQUIRE_SEARCHABLE_V1, SCAN_REVIEW_V1)
}
DEFAULT_POLICY_ID = PRESERVE_EXISTING_TEXT_V1.policy_id

_POLICY_ID = re.compile(r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-v([1-9][0-9]{0,5})")


def get_policy(policy_id: object, *, field: str = "policy_id") -> PolicyDefinition:
    """Return the definition for ``policy_id`` or raise :class:`ContractError`.

    A known policy name with an unsupported version is reported separately from
    an unknown name so that callers can tell "upgrade CHONK" from "typo".
    """
    if not isinstance(policy_id, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "policy_id must be a string.", field=field)
    policy = POLICIES.get(policy_id)
    if policy is not None:
        return policy
    match = _POLICY_ID.fullmatch(policy_id)
    if match and any(p.name == match.group(1) for p in POLICIES.values()):
        supported = ", ".join(sorted(POLICIES))
        raise ContractError(
            ErrorCode.UNSUPPORTED_POLICY_VERSION,
            f"This policy version is not supported. Supported policies: {supported}.",
            field=field,
        )
    raise ContractError(
        ErrorCode.UNKNOWN_POLICY,
        f"Unknown policy. Supported policies: {', '.join(sorted(POLICIES))}.",
        field=field,
    )
