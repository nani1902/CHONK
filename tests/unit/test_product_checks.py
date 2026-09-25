"""What the baseline product's own output checks catch, and what they miss.

``pdf_compressor.validate_pdf`` and ``compare_visual_similarity`` are the
only checks applied to candidates. The same deliberate damage used in
test_evaluation.py is fed to them here. Passing tests record guarantees the
migration must keep; known-gap tests record regressions they let through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import damage
from corpus import fixture_bytes
from corpus.pdfbuild import A4
from gaps import known_gap
from pdf_compressor import CompressionError, compare_visual_similarity, validate_pdf

MULTIPAGE = "text-multipage"
STATEMENT = "text-statement"


def write(directory: Path, name: str, data: bytes) -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def rejected_by_validate_pdf(path: Path, expected_pages: int) -> bool:
    try:
        validate_pdf(path, expected_pages)
    except CompressionError:
        return True
    return False


def page_count(name: str) -> int:
    return {"text-multipage": 12, "text-statement": 1, "form-acroform": 1}[name]


@pytest.mark.parametrize(
    ("apply", "message"),
    [
        (lambda d: damage.drop_page(d, 4), "page count changed"),
        (lambda d: damage.duplicate_page(d, 2), "page count changed"),
        (damage.truncate, "unreadable"),
    ],
    ids=["dropped-page", "duplicated-page", "truncated"],
)
def test_validate_pdf_rejects_page_count_changes_and_unreadable_output(tmp_path, apply, message):
    damaged = write(tmp_path, "damaged.pdf", apply(fixture_bytes(MULTIPAGE)))
    with pytest.raises(CompressionError, match=message):
        validate_pdf(damaged, 12)


def test_validate_pdf_accepts_an_identical_copy(tmp_path):
    validate_pdf(write(tmp_path, "copy.pdf", fixture_bytes(MULTIPAGE)), 12)


DAMAGE_VALIDATE_PDF_MISSES = [
    ("reordered-pages", MULTIPAGE, lambda d: damage.swap_pages(d, 1, 2)),
    ("rotated-page", MULTIPAGE, lambda d: damage.rotate_page(d, 0, 90)),
    ("resized-page", MULTIPAGE, lambda d: damage.resize_page(d, 1, *A4)),
    ("blank-page", MULTIPAGE, lambda d: damage.blank_page(d, 7)),
    ("changed-digit", STATEMENT, lambda d: damage.replace_text(d, 0, "7,347.15", "7,347.16")),
    ("text-layer-removed", STATEMENT, damage.strip_text_layer),
    ("form-values-removed", "form-acroform", damage.remove_widget_appearances),
]


@known_gap("CHONK-008", "validate_pdf checks only parseability, encryption, and page count")
@pytest.mark.parametrize(
    ("fixture", "apply"),
    [case[1:] for case in DAMAGE_VALIDATE_PDF_MISSES],
    ids=[case[0] for case in DAMAGE_VALIDATE_PDF_MISSES],
)
def test_validate_pdf_rejects_structural_and_text_damage(tmp_path, fixture, apply):
    damaged = write(tmp_path, "damaged.pdf", apply(fixture_bytes(fixture)))
    assert rejected_by_validate_pdf(damaged, page_count(fixture))


def test_similarity_is_one_for_an_identical_copy(tmp_path):
    source = write(tmp_path, "source.pdf", fixture_bytes(STATEMENT))
    copy = write(tmp_path, "copy.pdf", fixture_bytes(STATEMENT))
    assert compare_visual_similarity(source, copy, 72) == 1.0


def test_similarity_drops_for_a_blank_page(tmp_path):
    source = write(tmp_path, "source.pdf", fixture_bytes(STATEMENT))
    blank = write(tmp_path, "blank.pdf", damage.blank_page(fixture_bytes(STATEMENT), 0))
    assert compare_visual_similarity(source, blank, 72) < 1.0


@known_gap("CHONK-009", "a mean over pages dilutes one damaged page by the page count")
def test_one_damaged_page_scores_no_better_than_that_page_alone(tmp_path):
    multipage = fixture_bytes(MULTIPAGE)
    source = write(tmp_path, "source.pdf", multipage)
    damaged = write(tmp_path, "damaged.pdf", damage.blank_page(multipage, 7))
    single_page = damage.reorder_pages(multipage, [7])
    page_only = write(tmp_path, "page.pdf", single_page)
    page_blank = write(tmp_path, "page-blank.pdf", damage.blank_page(single_page, 0))

    document_score = compare_visual_similarity(source, damaged, 72)
    page_score = compare_visual_similarity(page_only, page_blank, 72)
    assert document_score <= page_score + 1e-9, (document_score, page_score)


@known_gap("CHONK-009", "render comparison never initializes forms, so field values are invisible")
def test_similarity_sees_form_field_values(tmp_path):
    source = write(tmp_path, "source.pdf", fixture_bytes("form-acroform"))
    emptied = write(
        tmp_path, "emptied.pdf", damage.remove_widget_appearances(fixture_bytes("form-acroform"))
    )
    assert compare_visual_similarity(source, emptied, 72) < 1.0
