"""Versioned policy definitions, feature evaluation, and check decisions."""

from __future__ import annotations

import pytest

from chonk.models import CheckId, CheckState, ContractError, ErrorCode, ReasonCode, ResultStatus
from chonk.policies import (
    DEFAULT_POLICY_ID,
    POLICIES,
    Feature,
    FeaturePresence,
    ImageOnlyPages,
    PolicyDefinition,
    get_policy,
)

PASS, FAIL, UNKNOWN, NA = (
    CheckState.PASS,
    CheckState.FAIL,
    CheckState.UNKNOWN,
    CheckState.NOT_APPLICABLE,
)
PRESERVE = POLICIES["preserve-existing-text-v1"]
SEARCHABLE = POLICIES["require-searchable-v1"]
SCAN = POLICIES["scan-review-v1"]


def all_pass(policy: PolicyDefinition, **overrides: CheckState) -> dict[CheckId, CheckState]:
    checks = {check: PASS for check in policy.checks}
    checks.update({CheckId(name): state for name, state in overrides.items()})
    return checks


def test_registry_contains_the_architecture_policies():
    assert set(POLICIES) == {
        "preserve-existing-text-v1",
        "require-searchable-v1",
        "scan-review-v1",
    }
    assert DEFAULT_POLICY_ID == "preserve-existing-text-v1"
    for policy_id, policy in POLICIES.items():
        assert policy.policy_id == policy_id
        assert get_policy(policy_id) is policy


@pytest.mark.parametrize("policy", POLICIES.values(), ids=lambda p: p.policy_id)
def test_size_and_feature_support_are_hard_in_every_policy(policy):
    assert policy.is_required(CheckId.SIZE_CEILING)
    assert policy.is_required(CheckId.FEATURE_SUPPORT)
    assert policy.is_required(CheckId.PAGE_STRUCTURE)
    assert CheckId.SIZE_CEILING not in policy.not_applicable_allowed
    assert CheckId.VISUAL_POLICY in policy.advisory_checks


def test_policies_differ_where_the_architecture_says_they_do():
    assert SEARCHABLE.is_required(CheckId.TEXT_LAYER)
    assert SEARCHABLE.image_only_pages is ImageOnlyPages.BLOCK
    assert not SEARCHABLE.not_applicable_allowed
    assert PRESERVE.is_required(CheckId.EXTRACTED_TEXT)
    assert CheckId.EXTRACTED_TEXT in PRESERVE.not_applicable_allowed
    assert CheckId.EXTRACTED_TEXT in SCAN.advisory_checks
    assert SCAN.image_only_pages is ImageOnlyPages.REVIEW


def test_policy_definitions_reject_weak_configurations():
    with pytest.raises(ValueError, match="size ceiling"):
        PolicyDefinition(
            name="weak",
            version=1,
            summary="",
            required_checks=(CheckId.FEATURE_SUPPORT,),
            advisory_checks=(CheckId.SIZE_CEILING,),
            not_applicable_allowed=frozenset(),
            blocked_features=frozenset(),
            image_only_pages=ImageOnlyPages.REVIEW,
        )
    with pytest.raises(ValueError, match="both required and advisory"):
        PolicyDefinition(
            name="overlap",
            version=1,
            summary="",
            required_checks=(CheckId.SIZE_CEILING, CheckId.FEATURE_SUPPORT),
            advisory_checks=(CheckId.SIZE_CEILING,),
            not_applicable_allowed=frozenset(),
            blocked_features=frozenset(),
            image_only_pages=ImageOnlyPages.REVIEW,
        )


@pytest.mark.parametrize(
    ("policy_id", "code"),
    [
        ("scan-review-v2", ErrorCode.UNSUPPORTED_POLICY_VERSION),
        ("scan-review", ErrorCode.UNKNOWN_POLICY),
        ("", ErrorCode.UNKNOWN_POLICY),
        (None, ErrorCode.INVALID_REQUEST),
        (1, ErrorCode.INVALID_REQUEST),
    ],
)
def test_get_policy_errors(policy_id, code):
    with pytest.raises(ContractError) as info:
        get_policy(policy_id)
    assert info.value.code is code
    assert info.value.field == "policy_id"


# --- Feature evaluation ---------------------------------------------------------


def inventory(**present: FeaturePresence) -> dict[Feature, FeaturePresence]:
    values = {feature: FeaturePresence.ABSENT for feature in Feature}
    values.update({Feature(name): state for name, state in present.items()})
    return values


def test_clean_inventory_passes():
    evaluation = PRESERVE.evaluate_features(inventory())
    assert evaluation.check_state is PASS
    assert evaluation.reason_codes == ()


@pytest.mark.parametrize(
    ("feature", "reason"),
    [
        ("encryption", ReasonCode.ENCRYPTED_INPUT),
        ("digital_signature", ReasonCode.SIGNED_INPUT),
        ("interactive_form", ReasonCode.UNSUPPORTED_FEATURE),
        ("xfa_form", ReasonCode.UNSUPPORTED_FEATURE),
        ("active_content", ReasonCode.UNSUPPORTED_FEATURE),
        ("embedded_files", ReasonCode.UNSUPPORTED_FEATURE),
        ("annotations", ReasonCode.UNSUPPORTED_FEATURE),
        ("links", ReasonCode.UNSUPPORTED_FEATURE),
        ("outlines", ReasonCode.UNSUPPORTED_FEATURE),
        ("tagged_structure", ReasonCode.UNSUPPORTED_FEATURE),
    ],
)
@pytest.mark.parametrize("policy", POLICIES.values(), ids=lambda p: p.policy_id)
def test_v1_blocks_every_feature_without_a_validator(policy, feature, reason):
    evaluation = policy.evaluate_features(inventory(**{feature: FeaturePresence.PRESENT}))
    assert evaluation.check_state is FAIL
    assert evaluation.reason_codes == (reason,)
    assert evaluation.blocking == (Feature(feature),)


def test_unknown_or_missing_inspection_is_never_absence():
    unknown = PRESERVE.evaluate_features(inventory(links=FeaturePresence.UNKNOWN))
    assert unknown.check_state is UNKNOWN
    assert unknown.reason_codes == (ReasonCode.INSPECTION_INCONCLUSIVE,)

    partial = {Feature.ENCRYPTION: FeaturePresence.ABSENT}
    missing = PRESERVE.evaluate_features(partial)
    assert missing.check_state is UNKNOWN
    assert set(missing.unknown) == set(Feature) - {Feature.ENCRYPTION}

    assert PRESERVE.evaluate_features({}).check_state is UNKNOWN


def test_blocking_and_unknown_features_are_both_reported():
    evaluation = PRESERVE.evaluate_features(
        inventory(encryption=FeaturePresence.PRESENT, outlines=FeaturePresence.UNKNOWN)
    )
    assert evaluation.check_state is FAIL
    assert evaluation.reason_codes == (
        ReasonCode.ENCRYPTED_INPUT,
        ReasonCode.INSPECTION_INCONCLUSIVE,
    )


# --- Decisions ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("policy", "checks", "status", "reasons"),
    [
        (PRESERVE, all_pass(PRESERVE), ResultStatus.READY, ()),
        (PRESERVE, all_pass(PRESERVE, extracted_text=NA), ResultStatus.READY, ()),
        (
            PRESERVE,
            all_pass(PRESERVE, visual_policy=UNKNOWN),
            ResultStatus.NEEDS_REVIEW,
            (ReasonCode.QUALITY_REVIEW_REQUIRED,),
        ),
        (
            PRESERVE,
            all_pass(PRESERVE, extracted_text=UNKNOWN, visual_policy=UNKNOWN),
            ResultStatus.BLOCKED,
            (ReasonCode.VALIDATION_INCONCLUSIVE,),
        ),
        (
            PRESERVE,
            all_pass(PRESERVE, extracted_text=FAIL),
            ResultStatus.BLOCKED,
            (ReasonCode.PRESERVATION_CONSTRAINT_FAILED,),
        ),
        (
            PRESERVE,
            all_pass(PRESERVE, size_ceiling=FAIL),
            ResultStatus.TARGET_NOT_MET,
            (ReasonCode.TARGET_NOT_MET,),
        ),
        (
            PRESERVE,
            all_pass(PRESERVE, size_ceiling=FAIL, page_structure=FAIL),
            ResultStatus.BLOCKED,
            (ReasonCode.PRESERVATION_CONSTRAINT_FAILED,),
        ),
        (
            PRESERVE,
            all_pass(PRESERVE, feature_support=UNKNOWN),
            ResultStatus.BLOCKED,
            (ReasonCode.INSPECTION_INCONCLUSIVE,),
        ),
        (
            PRESERVE,
            all_pass(PRESERVE, page_structure=NA),
            ResultStatus.BLOCKED,
            (ReasonCode.VALIDATION_INCONCLUSIVE,),
        ),
        (
            SEARCHABLE,
            all_pass(SEARCHABLE, text_layer=FAIL, extracted_text=FAIL),
            ResultStatus.BLOCKED,
            (ReasonCode.TEXT_LAYER_MISSING, ReasonCode.PRESERVATION_CONSTRAINT_FAILED),
        ),
        (
            SEARCHABLE,
            all_pass(SEARCHABLE, extracted_text=NA),
            ResultStatus.BLOCKED,
            (ReasonCode.VALIDATION_INCONCLUSIVE,),
        ),
        (
            SCAN,
            all_pass(SCAN, extracted_text=FAIL),
            ResultStatus.NEEDS_REVIEW,
            (ReasonCode.QUALITY_REVIEW_REQUIRED,),
        ),
        (
            SCAN,
            all_pass(SCAN, extracted_text=UNKNOWN, visual_policy=UNKNOWN),
            ResultStatus.NEEDS_REVIEW,
            (ReasonCode.VALIDATION_INCONCLUSIVE, ReasonCode.QUALITY_REVIEW_REQUIRED),
        ),
    ],
)
def test_decide(policy, checks, status, reasons):
    decision = policy.decide(checks)
    assert decision.status is status
    assert decision.reason_codes == reasons


def test_missing_checks_count_as_unknown():
    checks = all_pass(PRESERVE)
    del checks[CheckId.EXTRACTED_TEXT]
    assert PRESERVE.decide(checks).status is ResultStatus.BLOCKED
    checks = all_pass(PRESERVE)
    del checks[CheckId.VISUAL_POLICY]
    decision = PRESERVE.decide(checks)
    assert decision.status is ResultStatus.NEEDS_REVIEW
    assert decision.advisory_open == (CheckId.VISUAL_POLICY,)
