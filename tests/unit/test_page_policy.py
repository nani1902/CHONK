"""CHONK-007: the text-layer rule and how preflight applies it per policy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from chonk.inspection import (
    InspectionReport,
    PageInspection,
    PageKind,
    evaluate_preflight,
    evaluate_text_layer,
)
from chonk.models import CheckId, CheckState, ReasonCode
from chonk.policies import (
    POLICIES,
    PRESERVE_EXISTING_TEXT_V1,
    REQUIRE_SEARCHABLE_V1,
    SCAN_REVIEW_V1,
    Feature,
    FeaturePresence,
    ImageOnlyPages,
)

T, M, I, G, B, U = (
    PageKind.TEXT,
    PageKind.MIXED,
    PageKind.IMAGE_ONLY,
    PageKind.GRAPHICS_ONLY,
    PageKind.BLANK,
    PageKind.UNKNOWN,
)


def report(*kinds: PageKind, features: dict | None = None, **overrides) -> InspectionReport:
    inventory = {feature: FeaturePresence.ABSENT for feature in Feature}
    inventory.update(features or {})
    fields = dict(
        readable=True,
        page_count=len(kinds),
        features=inventory,
        evidence={},
        pages=tuple(PageInspection(index, kind, None) for index, kind in enumerate(kinds)),
        issues=(),
    )
    fields.update(overrides)
    return InspectionReport(**fields)


@pytest.mark.parametrize(
    ("kinds", "state", "without", "unknown"),
    [
        ((T,), CheckState.PASS, (), ()),
        ((T, M, B), CheckState.PASS, (), ()),
        ((M,), CheckState.PASS, (), ()),
        # A content page without a layer fails, wherever it is.
        ((T, I), CheckState.FAIL, (1,), ()),
        ((G, T), CheckState.FAIL, (0,), ()),
        ((I, B, G), CheckState.FAIL, (0, 2), ()),
        # Definite absence outranks uncertainty elsewhere.
        ((U, I), CheckState.FAIL, (1,), (0,)),
        # Nothing to search.
        ((B,), CheckState.FAIL, (), ()),
        ((B, B), CheckState.FAIL, (), ()),
        # Unclassified pages are never assumed to carry text.
        ((T, U), CheckState.UNKNOWN, (), (1,)),
        ((U,), CheckState.UNKNOWN, (), (0,)),
        ((B, U), CheckState.UNKNOWN, (), (1,)),
    ],
)
def test_text_layer_rule(kinds, state, without, unknown):
    result = evaluate_text_layer(report(*kinds))
    assert (result.state, result.pages_without_text, result.pages_unknown) == (state, without, unknown)


@pytest.mark.parametrize(
    "overrides",
    [
        {"readable": False, "page_count": None, "pages": ()},
        {"page_count": None, "pages": ()},  # content needed a password
        {"page_count": 3},  # fewer page reports than pages
    ],
    ids=["unreadable", "unclassified", "incomplete"],
)
def test_text_layer_is_unknown_without_a_full_page_classification(overrides):
    assert evaluate_text_layer(report(T, T, **overrides)).state is CheckState.UNKNOWN


def test_only_require_searchable_blocks_pages_without_text():
    blocking = {p.policy_id for p in POLICIES.values() if p.image_only_pages is ImageOnlyPages.BLOCK}
    assert blocking == {REQUIRE_SEARCHABLE_V1.policy_id}


@pytest.mark.parametrize(
    ("kinds", "allowed", "reasons", "state"),
    [
        ((T, B), True, (), CheckState.PASS),
        ((I,), False, (ReasonCode.TEXT_LAYER_MISSING,), CheckState.FAIL),
        ((G,), False, (ReasonCode.TEXT_LAYER_MISSING,), CheckState.FAIL),
        ((B,), False, (ReasonCode.TEXT_LAYER_MISSING,), CheckState.FAIL),
        ((T, U), False, (ReasonCode.VALIDATION_INCONCLUSIVE,), CheckState.UNKNOWN),
    ],
)
def test_require_searchable_preflight(kinds, allowed, reasons, state):
    decision = evaluate_preflight(report(*kinds), REQUIRE_SEARCHABLE_V1)
    assert decision.allowed is allowed
    assert decision.reason_codes == reasons
    assert decision.text_layer_required
    assert decision.checks == {CheckId.FEATURE_SUPPORT: CheckState.PASS, CheckId.TEXT_LAYER: state}


@pytest.mark.parametrize("policy", [PRESERVE_EXISTING_TEXT_V1, SCAN_REVIEW_V1], ids=lambda p: p.policy_id)
@pytest.mark.parametrize("kinds", [(I,), (G, B), (B,), (U,), (T, I)])
def test_review_policies_never_block_on_page_kinds(policy, kinds):
    decision = evaluate_preflight(report(*kinds), policy)
    assert decision.allowed and decision.reason_codes == ()
    assert not decision.text_layer_required
    assert decision.checks == {CheckId.FEATURE_SUPPORT: CheckState.PASS}


def test_missing_text_layer_is_reported_alongside_a_blocked_feature():
    scanned_form = report(I, features={Feature.INTERACTIVE_FORM: FeaturePresence.PRESENT})
    decision = evaluate_preflight(scanned_form, REQUIRE_SEARCHABLE_V1)
    assert not decision.allowed
    assert decision.reason_codes == (ReasonCode.UNSUPPORTED_FEATURE, ReasonCode.TEXT_LAYER_MISSING)


def test_uncertain_text_layer_adds_no_reason_when_something_else_blocks():
    encrypted = report(
        page_count=None,
        pages=(),
        features={Feature.ENCRYPTION: FeaturePresence.PRESENT},
    )
    decision = evaluate_preflight(encrypted, REQUIRE_SEARCHABLE_V1)
    assert decision.reason_codes == (ReasonCode.ENCRYPTED_INPUT,)
    assert decision.checks[CheckId.TEXT_LAYER] is CheckState.UNKNOWN


def test_text_layer_alone_never_passes_blocked_features():
    decision = evaluate_preflight(
        report(T, features={Feature.LINKS: FeaturePresence.UNKNOWN}), REQUIRE_SEARCHABLE_V1
    )
    assert decision.text_layer.state is CheckState.PASS
    assert not decision.allowed


def test_a_policy_that_blocks_pages_without_text_must_require_the_text_layer_check():
    with pytest.raises(ValueError, match="text_layer"):
        replace(
            REQUIRE_SEARCHABLE_V1,
            required_checks=tuple(
                c for c in REQUIRE_SEARCHABLE_V1.required_checks if c is not CheckId.TEXT_LAYER
            ),
        )
