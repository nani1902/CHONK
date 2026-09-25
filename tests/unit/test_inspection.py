"""Preflight inspection (CHONK-005): feature inventory, uncertainty, and pages.

The corpus fixtures declare their features in ``tests/corpus/catalog.py``;
these tests check inspection against those declarations rather than against
its own output. Small documents built inline cover detector paths the corpus
does not need a whole fixture for.
"""

from __future__ import annotations

import re
import time

import pytest

from chonk import inspection
from chonk.inspection import (
    BLOCKING_ISSUES,
    InspectionIssue,
    InspectionLimits,
    PageKind,
    evaluate_preflight,
    inspect_pdf,
)
from chonk.models import CheckState, ReasonCode
from chonk.policies import DEFAULT_POLICY_ID, POLICIES, Feature, FeaturePresence
from corpus import BY_NAME, FIXTURES, SYNTHETIC_MARKER, fixture_bytes
from corpus.pdfbuild import Document, Name, Stream, TextLine, font_resources, text_operators
from support import FileState

PRESENT = FeaturePresence.PRESENT
ABSENT = FeaturePresence.ABSENT
UNKNOWN = FeaturePresence.UNKNOWN
DEFAULT_POLICY = POLICIES[DEFAULT_POLICY_ID]

# Corpus feature tags that name an inspected document feature.
TAG_FEATURES = {
    "encrypted": {Feature.ENCRYPTION},
    "acroform": {Feature.INTERACTIVE_FORM},
    "widgets": {Feature.INTERACTIVE_FORM},
    "signature": {Feature.DIGITAL_SIGNATURE},
    "xfa": {Feature.XFA_FORM},
    "annotations": {Feature.ANNOTATIONS},
    "link": {Feature.LINKS},
    "outline": {Feature.OUTLINES},
    "javascript": {Feature.ACTIVE_CONTENT},
    "launch-action": {Feature.ACTIVE_CONTENT},
    "embedded-file": {Feature.EMBEDDED_FILES},
    "tagged": {Feature.TAGGED_STRUCTURE},
    "optional-content": {Feature.OPTIONAL_CONTENT},
}

EXPECTED_PAGE_KINDS = {
    "text-statement": [PageKind.TEXT],
    "text-multipage": [PageKind.TEXT] * 12,
    "text-tiny": [PageKind.TEXT],
    "image-scan": [PageKind.IMAGE_ONLY] * 2,
    "image-mixed": [PageKind.MIXED] * 2,
    "already-small": [PageKind.TEXT],
    "blank-page": [PageKind.TEXT, PageKind.BLANK, PageKind.TEXT],
    "vector-only": [PageKind.GRAPHICS_ONLY],
    "text-unmapped": [PageKind.UNKNOWN],
    "malformed-bad-xref": [PageKind.TEXT],
}


def report_for(name: str, **kwargs):
    return inspect_pdf(fixture_bytes(name), **kwargs)


def decision_status(report) -> str:
    decision = evaluate_preflight(report, DEFAULT_POLICY)
    if decision.allowed:
        return "supported"
    if not report.readable:
        return "unreadable"
    return "blocked" if decision.blocking_features else "inconclusive"


# --- The corpus, against its own declarations -----------------------------------------


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda spec: spec.name)
def test_preflight_decision_matches_the_catalog(spec):
    assert decision_status(report_for(spec.name)) == spec.preflight


@pytest.mark.parametrize(
    "spec",
    [s for s in FIXTURES if s.preflight in ("supported", "blocked") and s.name != "encrypted-user-password"],
    ids=lambda spec: spec.name,
)
def test_detected_features_are_exactly_the_declared_ones(spec):
    expected = set().union(*(TAG_FEATURES.get(tag, set()) for tag in spec.features))
    report = report_for(spec.name)
    assert set(report.present_features) == expected
    assert report.unknown_features == ()


@pytest.mark.parametrize("name", sorted(EXPECTED_PAGE_KINDS))
def test_pages_are_classified_conservatively(name):
    report = report_for(name)
    assert [page.kind for page in report.pages] == EXPECTED_PAGE_KINDS[name]
    assert report.page_count == len(EXPECTED_PAGE_KINDS[name])


def test_page_counts_explain_the_classification():
    scan = report_for("image-scan").pages[0]
    assert scan.text_chars == 0 and scan.image_objects == 1
    unmapped = report_for("text-unmapped").pages[0]
    assert unmapped.text_objects == 1 and unmapped.text_chars == 0
    assert unmapped.unmappable_chars == 6
    chart = report_for("vector-only").pages[0]
    assert chart.text_objects == 0 and chart.image_objects == 0 and chart.vector_objects > 10


def test_page_geometry_is_recorded():
    pages = report_for("text-multipage").pages
    assert pages[0].geometry.displayed_size == (612, 792)
    assert pages[3].geometry.displayed_size == pytest.approx((595.276, 841.89))
    assert pages[6].geometry.displayed_size == (792, 612)
    assert pages[9].geometry.rotation == 90
    assert pages[9].geometry.displayed_size == (792, 612)
    assert {page.geometry.user_unit for page in pages} == {1.0}


def test_text_layer_coverage_counts():
    counts = report_for("blank-page").page_kind_counts()
    assert counts[PageKind.TEXT] == 2 and counts[PageKind.BLANK] == 1
    assert sum(counts.values()) == 3


# --- Unknown is never absent -------------------------------------------------------------


def test_unreadable_input_is_malformed_not_featureless():
    for name in ("malformed-not-pdf", "malformed-truncated"):
        report = report_for(name)
        assert not report.readable and report.page_count is None
        assert set(report.unknown_features) == set(Feature)
        decision = evaluate_preflight(report, DEFAULT_POLICY)
        assert decision.feature_support is CheckState.FAIL
        assert decision.reason_codes == (ReasonCode.MALFORMED_INPUT,)


def test_password_protected_content_is_not_inspected():
    report = report_for("encrypted-user-password")
    assert report.present_features == (Feature.ENCRYPTION,)
    assert set(report.unknown_features) == set(Feature) - {Feature.ENCRYPTION}
    assert report.issues == (InspectionIssue.CONTENT_ENCRYPTED,)
    assert report.pages == () and report.page_count is None
    decision = evaluate_preflight(report, DEFAULT_POLICY)
    assert decision.reason_codes == (ReasonCode.ENCRYPTED_INPUT, ReasonCode.INSPECTION_INCONCLUSIVE)


def test_encryption_without_a_user_password_is_still_encryption():
    report = report_for("encrypted-owner-only")
    assert report.present_features == (Feature.ENCRYPTION,)
    assert [page.kind for page in report.pages] == [PageKind.TEXT]


def test_an_unreferenced_script_makes_active_content_unknown():
    report = report_for("orphan-javascript")
    assert report.presence(Feature.ACTIVE_CONTENT) is UNKNOWN
    assert "sweep.unreferenced_indicator" in report.evidence[Feature.ACTIVE_CONTENT]
    decision = evaluate_preflight(report, DEFAULT_POLICY)
    assert decision.feature_support is CheckState.UNKNOWN
    assert decision.reason_codes == (ReasonCode.INSPECTION_INCONCLUSIVE,)
    assert decision.unknown_features == (Feature.ACTIVE_CONTENT,)


def test_a_script_outside_the_cross_reference_table_is_found_by_the_raw_scan():
    appended = fixture_bytes("already-small") + b"\n99 0 obj\n<< /S /JavaScript /JS (x) >>\nendobj\n"
    report = inspect_pdf(appended)
    assert report.presence(Feature.ACTIVE_CONTENT) is UNKNOWN
    assert report.evidence[Feature.ACTIVE_CONTENT] == ("raw.unreferenced_token",)


def test_a_hex_escaped_name_is_found_by_the_object_sweep():
    data = one_page(objects=lambda d: d.add({"S": Name("PLACEHOLDERX")}))
    data = data.replace(b"/PLACEHOLDERX", b"/Java#53cript")
    report = inspect_pdf(data)
    assert report.evidence[Feature.ACTIVE_CONTENT] == ("sweep.unreferenced_indicator",)


def test_raw_tokens_inside_stream_data_are_ignored():
    content = text_operators([TextLine(72, 700, 12, "Scripts use /JavaScript and /JS")])
    report = inspect_pdf(one_page(content))
    assert report.presence(Feature.ACTIVE_CONTENT) is ABSENT


def test_unrecognized_actions_and_annotations_are_unknown_active_content():
    def link(d):
        action = d.add({"S": Name("FutureAction")})
        return {"page": {"Annots": [d.add({"Type": Name("Annot"), "Subtype": Name("Link"),
                                          "Rect": [0, 0, 10, 10], "A": action})]}}

    report = inspect_pdf(one_page(objects=link))
    assert report.presence(Feature.LINKS) is PRESENT
    assert report.presence(Feature.ACTIVE_CONTENT) is UNKNOWN
    assert report.evidence[Feature.ACTIVE_CONTENT] == ("annotation.action.unrecognized",)

    def odd(d):
        return {"page": {"Annots": [d.add({"Type": Name("Annot"), "Subtype": Name("Hologram"),
                                          "Rect": [0, 0, 10, 10]})]}}

    report = inspect_pdf(one_page(objects=odd))
    assert report.presence(Feature.ANNOTATIONS) is PRESENT
    assert report.presence(Feature.ACTIVE_CONTENT) is UNKNOWN


def test_malformed_structures_are_unknown():
    report = inspect_pdf(one_page(page_extra={"Annots": 42}))
    for feature in (Feature.ANNOTATIONS, Feature.LINKS, Feature.INTERACTIVE_FORM):
        assert report.presence(feature) is UNKNOWN

    report = inspect_pdf(one_page(catalog_extra={"AcroForm": {"Fields": 7}}))
    assert report.presence(Feature.INTERACTIVE_FORM) is UNKNOWN
    assert report.presence(Feature.DIGITAL_SIGNATURE) is UNKNOWN


def test_append_only_signature_flag_without_a_signature_is_unknown():
    report = inspect_pdf(one_page(catalog_extra={"AcroForm": {"Fields": [], "SigFlags": 3}}))
    assert report.presence(Feature.DIGITAL_SIGNATURE) is UNKNOWN
    assert report.presence(Feature.INTERACTIVE_FORM) is ABSENT


def test_an_empty_acroform_and_empty_name_trees_are_absent():
    report = inspect_pdf(
        one_page(catalog_extra={
            "AcroForm": {"Fields": []},
            "Names": {"JavaScript": {"Names": []}, "EmbeddedFiles": {"Names": []}},
        })
    )
    assert report.present_features == () and report.unknown_features == ()


def test_a_destination_open_action_is_not_active_content():
    report = inspect_pdf(one_page(open_action_destination=True))
    assert report.present_features == () and report.unknown_features == ()


def test_automatic_external_navigation_is_active_content():
    report = inspect_pdf(
        one_page(catalog_extra={"OpenAction": {"S": Name("URI"), "URI": "https://example.invalid/"}})
    )
    assert report.presence(Feature.ACTIVE_CONTENT) is PRESENT
    assert report.presence(Feature.LINKS) is PRESENT


def test_action_chains_are_followed():
    report = inspect_pdf(
        one_page(catalog_extra={"OpenAction": {
            "S": Name("GoTo"), "D": [0, Name("Fit")],
            "Next": [{"S": Name("Named"), "N": Name("NextPage")},
                     {"S": Name("Launch"), "F": "synthetic.invalid"}],
        }})
    )
    assert report.evidence[Feature.ACTIVE_CONTENT] == ("catalog.open_action",)


def test_reference_cycles_terminate():
    def cyclic(d):
        first = d.reserve()
        second = d.add({"S": Name("GoTo"), "D": [0, Name("Fit")], "Next": first})
        d.set(first, {"S": Name("GoTo"), "D": [0, Name("Fit")], "Next": second})
        return {"catalog": {"OpenAction": first}}

    report = inspect_pdf(one_page(objects=cyclic))
    assert report.present_features == () and report.unknown_features == ()


@pytest.mark.parametrize(
    ("limits", "unknown"),
    [
        (InspectionLimits(max_objects=1), set(inspection.SWEPT_FEATURES)),
        (InspectionLimits(max_tree_nodes=1), {Feature.ACTIVE_CONTENT, Feature.LINKS, Feature.EMBEDDED_FILES}),
    ],
    ids=["objects", "tree-nodes"],
)
def test_reaching_a_limit_is_unknown_not_absent(limits, unknown):
    report = report_for("outline", limits=limits)
    assert InspectionIssue.LIMIT_REACHED in report.issues
    assert set(report.unknown_features) == unknown
    assert report.presence(Feature.OUTLINES) is PRESENT


def test_page_limit_leaves_later_pages_unknown():
    report = report_for("text-multipage", limits=InspectionLimits(max_pages=2))
    assert [page.kind for page in report.pages[:3]] == [PageKind.TEXT, PageKind.TEXT, PageKind.UNKNOWN]
    assert InspectionIssue.LIMIT_REACHED in report.issues
    assert report.presence(Feature.ANNOTATIONS) is UNKNOWN


def test_page_count_disagreement_is_inconclusive():
    report = inspect_pdf(one_page(pages_count=3))
    assert InspectionIssue.PAGE_COUNT_MISMATCH in report.issues
    assert all(page.kind is PageKind.UNKNOWN for page in report.pages)
    decision = evaluate_preflight(report, DEFAULT_POLICY)
    assert decision.feature_support is CheckState.UNKNOWN
    assert decision.reason_codes == (ReasonCode.INSPECTION_INCONCLUSIVE,)
    assert decision.blocking_issues == (InspectionIssue.PAGE_COUNT_MISMATCH,)


def test_renderer_failure_is_inconclusive(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr(inspection.pdfium, "PdfDocument", refuse)
    report = report_for("text-statement")
    assert report.issues == (InspectionIssue.RENDERER_UNREADABLE,)
    assert [page.kind for page in report.pages] == [PageKind.UNKNOWN]
    assert not evaluate_preflight(report, DEFAULT_POLICY).allowed


def test_a_failing_detector_reports_unknown(monkeypatch):
    def broken(self, root):
        raise RuntimeError("detector bug")

    monkeypatch.setattr(inspection._Walker, "_outlines", broken)
    report = report_for("text-statement")
    assert set(report.unknown_features) == {
        Feature.OUTLINES, Feature.ACTIVE_CONTENT, Feature.LINKS, Feature.EMBEDDED_FILES
    }
    assert report.evidence[Feature.OUTLINES] == ("outlines.error",)


def test_a_document_without_pages_is_unreadable():
    document = Document()
    pages = document.add({"Type": Name("Pages"), "Kids": [], "Count": 0})
    root = document.add({"Type": Name("Catalog"), "Pages": pages})
    report = inspect_pdf(document.to_bytes(root, b"0" * 16))
    assert not report.readable and report.issues == (InspectionIssue.NO_PAGES,)


def test_repaired_structure_is_reported_but_still_inspected():
    report = report_for("malformed-bad-xref")
    assert report.issues == (InspectionIssue.PARSER_RECOVERED,)
    assert InspectionIssue.PARSER_RECOVERED not in BLOCKING_ISSUES
    assert evaluate_preflight(report, DEFAULT_POLICY).allowed


# --- Hostile structure stays bounded -----------------------------------------------------------


def test_raw_scan_ignores_stream_data_and_is_linear():
    spans = list(inspection._structure_spans(b"a /JS stream\n/JS endstream /XFA streams /x"))
    scanned = b"".join(b"a /JS stream\n/JS endstream /XFA streams /x"[s:e] for s, e in spans)
    assert b"/XFA" in scanned and scanned.count(b"/JS") == 1

    # Unterminated stream keywords must not make the scan quadratic.
    hostile = b"stream\n" * 300_000 + b"/JavaScript "
    started = time.perf_counter()
    assert inspection._raw_scan(hostile, set()) == {Feature.ACTIVE_CONTENT}
    assert time.perf_counter() - started < 2


def test_null_entries_are_absent():
    report = inspect_pdf(one_page(page_extra={"AA": None}, catalog_extra={"OpenAction": None}))
    assert report.present_features == () and report.unknown_features == ()


def test_a_cyclic_field_parent_chain_terminates():
    def cyclic(d):
        field = d.reserve()
        widget = d.add({"Type": Name("Annot"), "Subtype": Name("Widget"), "Rect": [0, 0, 10, 10],
                        "Parent": field})
        d.set(field, {"FT": Name("Tx"), "T": "loop", "Kids": [widget], "Parent": field})
        return {"page": {"Annots": [widget]}, "catalog": {"AcroForm": {"Fields": [field]}}}

    report = inspect_pdf(one_page(objects=cyclic))
    assert report.presence(Feature.INTERACTIVE_FORM) is PRESENT
    assert report.presence(Feature.DIGITAL_SIGNATURE) is ABSENT


def test_blank_check_render_is_bounded_on_huge_pages():
    started = time.perf_counter()
    report = inspect_pdf(one_page(b"", page_extra={"MediaBox": [0, 0, 14400, 14400]}))
    assert report.pages[0].kind is PageKind.BLANK
    assert time.perf_counter() - started < 5


# --- Page classification edge cases ---------------------------------------------------------


def test_a_page_drawn_only_by_an_annotation_is_not_blank():
    def square(d):
        appearance = d.add(Stream(
            {"Type": Name("XObject"), "Subtype": Name("Form"), "BBox": [0, 0, 100, 100]},
            b"0 0 1 rg 0 0 100 100 re f",
        ))
        return {"page": {"Annots": [d.add({"Type": Name("Annot"), "Subtype": Name("Square"),
                                          "Rect": [100, 100, 200, 200], "F": 4,
                                          "AP": {"N": appearance}})]}}

    assert inspect_pdf(one_page(b"", objects=square)).pages[0].kind is PageKind.UNKNOWN


@pytest.mark.parametrize(
    ("content", "kind"),
    [
        (text_operators([TextLine(72, 700, 12, "   ")]), PageKind.BLANK),
        (b"q 1 g 0 0 612 792 re f Q", PageKind.BLANK),
        (b"q 0.5 g 0 0 612 792 re f Q BT 3 Tr /F1 12 Tf 72 700 Td (OCR words) Tj ET", PageKind.TEXT),
    ],
    ids=["whitespace-text", "white-rectangle", "invisible-text"],
)
def test_visible_content_decides_blankness_and_invisible_text_is_a_text_layer(content, kind):
    assert inspect_pdf(one_page(content)).pages[0].kind is kind


# --- Reports hold no content, and inspection changes nothing -----------------------------


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda spec: spec.name)
def test_reports_contain_no_document_content(spec):
    report = report_for(spec.name)
    assert SYNTHETIC_MARKER not in repr(report)
    for bases in report.evidence.values():
        for basis in bases:
            assert re.fullmatch(r"[a-z_]+(\.[a-z_]+)*", basis)


def test_inspection_leaves_the_file_unchanged(corpus_dir, tmp_path):
    source = tmp_path / "signed.pdf"
    source.write_bytes((corpus_dir / BY_NAME["signed-pkcs7"].filename).read_bytes())
    before = FileState.of(source)
    inspect_pdf(source)
    assert FileState.of(source) == before


def test_parser_warnings_are_counted_not_printed(capfd):
    inspect_pdf(fixture_bytes("malformed-bad-xref"))
    assert capfd.readouterr() == ("", "")


def test_blocking_issues_and_policy_agree():
    report = report_for("text-statement")
    for policy in POLICIES.values():
        decision = evaluate_preflight(report, policy)
        assert decision.allowed and decision.policy_id == policy.policy_id


# --- Inline documents ---------------------------------------------------------------------------


def one_page(
    content: bytes | None = None,
    *,
    page_extra: dict | None = None,
    catalog_extra: dict | None = None,
    objects=None,
    pages_count: int = 1,
    open_action_destination: bool = False,
) -> bytes:
    """A one-page PDF. ``objects(document)`` may add objects and return
    ``{"page": {...}, "catalog": {...}}`` entries to merge."""
    document = Document()
    fonts = font_resources(document)
    extra = (objects(document) if objects else None) or {}
    if not isinstance(extra, dict):
        extra = {}
    pages = document.reserve()
    page = document.reserve()
    if content is None:
        content = text_operators([TextLine(72, 700, 12, "Plain page")])
    entries = {
        "Type": Name("Page"),
        "Parent": pages,
        "MediaBox": [0, 0, 612, 792],
        "Resources": {"Font": dict(fonts)},
        "Contents": document.add(Stream({}, content)),
    }
    entries.update(page_extra or {})
    entries.update(extra.get("page", {}))
    document.set(page, entries)
    document.set(pages, {"Type": Name("Pages"), "Kids": [page], "Count": pages_count})
    catalog = {"Type": Name("Catalog"), "Pages": pages}
    if open_action_destination:
        catalog["OpenAction"] = [page, Name("Fit")]
    catalog.update(catalog_extra or {})
    catalog.update(extra.get("catalog", {}))
    return document.to_bytes(document.add(catalog), b"0" * 16)
