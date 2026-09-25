"""The evaluation suite detects the regressions it exists to catch.

Each case applies deliberate damage to a fixture and requires the evaluation
checks in tests/evaluation.py to flag it with the expected check. Undamaged
copies must pass, so a check that flags everything cannot pass this file.
"""

from __future__ import annotations

import pytest

import damage
from corpus import FIXTURES, fixture_bytes
from corpus.pdfbuild import A4
from evaluation import VISUAL_FLOOR, evaluate

MULTIPAGE = "text-multipage"
STATEMENT = "text-statement"
SCAN = "image-scan"

DAMAGE_CASES = [
    # (id, fixture, damage function, checks that must be flagged)
    ("dropped-page", MULTIPAGE, lambda d: damage.drop_page(d, 4), {"page_count"}),
    ("duplicated-page", MULTIPAGE, lambda d: damage.duplicate_page(d, 2), {"page_count"}),
    ("reordered-pages", MULTIPAGE, lambda d: damage.swap_pages(d, 1, 2), {"text"}),
    ("rotated-page", MULTIPAGE, lambda d: damage.rotate_page(d, 0, 90), {"page_geometry", "visual"}),
    ("upside-down-page", MULTIPAGE, lambda d: damage.rotate_page(d, 0, 180), {"visual"}),
    ("resized-page", MULTIPAGE, lambda d: damage.resize_page(d, 1, *A4), {"page_geometry"}),
    ("one-blank-page-of-twelve", MULTIPAGE, lambda d: damage.blank_page(d, 7), {"text", "visual"}),
    (
        "changed-digit",
        STATEMENT,
        lambda d: damage.replace_text(d, 0, "7,347.15", "7,347.16"),
        {"text"},
    ),
    ("text-layer-removed", STATEMENT, damage.strip_text_layer, {"text"}),
    ("obscured-scan-region", SCAN, lambda d: damage.cover_region(d, 1, (200, 300, 90, 40)), {"visual"}),
    ("blank-scan-page", SCAN, lambda d: damage.blank_page(d, 0), {"visual"}),
    ("form-values-removed", "form-acroform", damage.remove_widget_appearances, {"form_fields", "visual"}),
    ("truncated-output", STATEMENT, damage.truncate, {"parse"}),
]


@pytest.mark.parametrize(
    ("fixture", "apply", "expected"),
    [case[1:] for case in DAMAGE_CASES],
    ids=[case[0] for case in DAMAGE_CASES],
)
def test_evaluation_flags_deliberate_damage(fixture, apply, expected):
    source = fixture_bytes(fixture)
    damaged = apply(source)
    assert damaged != source
    result = evaluate(source, damaged)
    assert expected <= result.failed_checks, result.findings


@pytest.mark.parametrize(
    "name",
    [s.name for s in FIXTURES if s.baseline_accepts],
)
def test_an_identical_copy_passes_every_check(name):
    source = fixture_bytes(name)
    result = evaluate(source, bytes(source), target_bytes=len(source))
    assert result.passed, result.findings
    assert min(result.page_similarity) == 1.0


def test_single_damaged_page_is_located_not_averaged_away():
    source = fixture_bytes(MULTIPAGE)
    result = evaluate(source, damage.blank_page(source, 7))
    visual_pages = {f.page for f in result.findings if f.check == "visual"}
    assert visual_pages == {8}
    undamaged = [s for i, s in enumerate(result.page_similarity) if i != 7]
    assert min(undamaged) == 1.0


def test_changed_digit_is_invisible_to_rendering_alone():
    """Why the text check exists: a one-digit change barely moves the pixels."""
    source = fixture_bytes(STATEMENT)
    result = evaluate(source, damage.replace_text(source, 0, "7,347.15", "7,347.16"))
    assert result.failed_checks == {"text"}
    assert min(result.page_similarity) >= VISUAL_FLOOR


def test_size_ceiling_is_exact():
    source = fixture_bytes(STATEMENT)
    assert evaluate(source, source, target_bytes=len(source)).passed
    over = evaluate(source, source, target_bytes=len(source) - 1)
    assert over.failed_checks == {"size_ceiling"}


def test_evaluation_rejects_a_lost_signature():
    source = fixture_bytes("signed-pkcs7")
    rewritten = damage.reorder_pages(source, [0])  # any rewrite breaks the ByteRange
    assert "signature" in evaluate(source, rewritten).failed_checks
