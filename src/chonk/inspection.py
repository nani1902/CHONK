"""Preflight inspection: what a source PDF contains, before any rewrite.

:func:`inspect_pdf` reads a PDF without modifying it and returns an
:class:`InspectionReport`: a document-feature inventory, per-page geometry and
content classification, and any condition that limited the inspection.
:func:`evaluate_preflight` applies a policy's feature restrictions to a report;
the engine refuses to run a backend unless the result is ``pass``.

The inventory is conservative. Each feature is ``present``, ``absent``, or
``unknown``, and ``absent`` is reported only when every applicable detector ran
to completion and found nothing:

* A detector that fails, meets a malformed structure, an unrecognized action,
  or a size limit reports ``unknown`` for its features, never ``absent``.
* Three independent views are combined. A structural walk from the document
  catalog decides presence. A sweep of every indirect object in the
  cross-reference table and a scan of the raw bytes outside stream data find
  high-risk indicators (signatures, scripts and other active content,
  attachments, XFA, optional content, encryption) that the walk did not
  reach; the raw scan counts only tokens that no parsed object contains. Such an indicator makes the feature ``unknown``: an unreferenced
  object is not used by this parser's view of the document, but another
  parser, a repaired cross-reference table, or an earlier revision may use it.
* Content that cannot be decrypted without a password is not inspected; every
  feature other than encryption is then ``unknown``.

Page classification uses PDFium, the same renderer as visual comparison. A
page is ``text`` or ``mixed`` only when PDFium extracts at least one usable
character, ``blank`` only when a render is visibly white, and ``unknown``
when text objects yield only unmappable characters or content is drawn that
no recognized object explains. Extracted text is never retained: reports hold
counts, enumerations, geometry, and fixed evidence codes, never document
content, names, or metadata.

Detection is not proof of safety. The report says what CHONK found and how
certain it is; see ``docs/product/SUPPORTED_FEATURES.md`` for the matrix of
features, detectors, and policy treatment.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

from chonk.models import CheckState, CompressionError, ReasonCode
from chonk.policies import Feature, FeaturePresence, PolicyDefinition

try:
    from pypdf import PdfReader
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        IndirectObject,
        NameObject,
        NullObject,
    )
except ImportError:  # A friendly error is reported when the reader is needed.
    PdfReader = None  # type: ignore[assignment]

try:
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c
except ImportError:  # A friendly error is reported when the renderer is needed.
    pdfium = pdfium_c = None  # type: ignore[assignment]

try:
    from PIL import ImageStat
except ImportError:  # A friendly error is reported when the renderer is needed.
    ImageStat = None  # type: ignore[assignment]

INSPECTOR_VERSION = "1"
"""Bumped whenever detection or classification changes what a report can say."""


# --- Report types ---------------------------------------------------------------


class PageKind(str, Enum):
    """What a page carries, as far as CHONK can tell.

    Pages without a usable text layer are ``image_only``, ``graphics_only``, or
    ``blank``; policies decide whether they need review or block processing.
    """

    TEXT = "text"
    """Usable extracted text and no raster images."""
    MIXED = "mixed"
    """Usable extracted text and at least one raster image (for example a
    photograph, or a scan with an OCR text layer)."""
    IMAGE_ONLY = "image_only"
    """Raster images and no text layer, typically a scan."""
    GRAPHICS_ONLY = "graphics_only"
    """Vector drawing and no text layer or images, for example a chart or
    text converted to outlines."""
    BLANK = "blank"
    """No text layer, no images, and a render that is visibly white."""
    UNKNOWN = "unknown"
    """Classification failed, text objects produced no usable characters, or
    something visible was drawn that no recognized object explains."""


class InspectionIssue(str, Enum):
    """A condition that limited inspection. Values are stable codes."""

    UNREADABLE = "unreadable"
    """The file could not be parsed as a PDF."""
    NO_PAGES = "no_pages"
    """The PDF parsed but has no pages."""
    CONTENT_ENCRYPTED = "content_encrypted"
    """Encrypted content needs a password; features other than encryption and
    all pages were not inspected."""
    PARSER_RECOVERED = "parser_recovered"
    """The parser reported damage it worked around, such as a broken
    cross-reference table. Informational: detection still ran."""
    OBJECTS_UNREADABLE = "objects_unreadable"
    """Some indirect objects could not be parsed, so the object sweep is
    incomplete."""
    LIMIT_REACHED = "limit_reached"
    """An inspection limit stopped a detector before it finished."""
    RENDERER_UNREADABLE = "renderer_unreadable"
    """PDFium could not open the file, so pages were not classified and visual
    comparison would be impossible."""
    PAGE_COUNT_MISMATCH = "page_count_mismatch"
    """The structural parser and the renderer disagree about the page count."""


BLOCKING_ISSUES = frozenset(
    {InspectionIssue.RENDERER_UNREADABLE, InspectionIssue.PAGE_COUNT_MISMATCH}
)
"""Issues that make the whole inspection inconclusive, whatever the feature
inventory says. The other issues act through ``unknown`` features, or through
``readable`` for unreadable and page-less input."""


@dataclass(frozen=True)
class InspectionLimits:
    """Bounds on inspection work. Reaching one yields ``unknown``, not ``absent``."""

    max_pages: int = 10_000
    max_objects: int = 1_000_000
    """Indirect objects examined by the sweep."""
    max_tree_nodes: int = 100_000
    """Nodes visited in any one form-field, outline, or action structure."""
    max_form_depth: int = 16
    """Nesting depth of form XObjects searched for page content."""
    blank_render_dpi: int = 72
    """Resolution of the render that confirms a page is blank."""


DEFAULT_LIMITS = InspectionLimits()


@dataclass(frozen=True)
class PageGeometry:
    """Page boxes in PDF user-space units, before rotation and ``user_unit``."""

    media_box: tuple[float, float, float, float]
    crop_box: tuple[float, float, float, float]
    rotation: int
    user_unit: float

    @property
    def displayed_size(self) -> tuple[float, float]:
        """Crop-box width and height in points as displayed, after rotation."""
        width = abs(self.crop_box[2] - self.crop_box[0]) * self.user_unit
        height = abs(self.crop_box[3] - self.crop_box[1]) * self.user_unit
        return (height, width) if self.rotation % 180 else (width, height)


@dataclass(frozen=True)
class PageInspection:
    index: int
    """Zero-based page index."""
    kind: PageKind
    geometry: PageGeometry | None
    """``None`` if the page boxes could not be read."""
    text_chars: int | None = None
    """Usable extracted characters (letters, digits, punctuation, symbols)."""
    unmappable_chars: int | None = None
    """Extracted characters with no usable Unicode value."""
    text_objects: int | None = None
    image_objects: int | None = None
    vector_objects: int | None = None
    """Path and shading objects."""


@dataclass(frozen=True)
class InspectionReport:
    """Content-free result of preflight inspection."""

    readable: bool
    page_count: int | None
    features: Mapping[Feature, FeaturePresence]
    evidence: Mapping[Feature, tuple[str, ...]]
    """For each present or unknown feature, fixed codes naming what the
    detectors found; never document content."""
    pages: tuple[PageInspection, ...]
    issues: tuple[InspectionIssue, ...]
    inspector_version: str = INSPECTOR_VERSION

    def presence(self, feature: Feature) -> FeaturePresence:
        return self.features.get(feature, FeaturePresence.UNKNOWN)

    @property
    def present_features(self) -> tuple[Feature, ...]:
        return tuple(f for f in Feature if self.presence(f) is FeaturePresence.PRESENT)

    @property
    def unknown_features(self) -> tuple[Feature, ...]:
        return tuple(f for f in Feature if self.presence(f) is FeaturePresence.UNKNOWN)

    def page_kind_counts(self) -> dict[PageKind, int]:
        counts = {kind: 0 for kind in PageKind}
        for page in self.pages:
            counts[page.kind] += 1
        return counts


@dataclass(frozen=True)
class PreflightDecision:
    """A policy's verdict on an inspection report, before any rewrite."""

    policy_id: str
    feature_support: CheckState
    """The ``feature_support`` check. Only ``pass`` allows processing."""
    reason_codes: tuple[ReasonCode, ...]
    blocking_features: tuple[Feature, ...]
    unknown_features: tuple[Feature, ...]
    blocking_issues: tuple[InspectionIssue, ...]

    @property
    def allowed(self) -> bool:
        return self.feature_support is CheckState.PASS


def evaluate_preflight(report: InspectionReport, policy: PolicyDefinition) -> PreflightDecision:
    """Apply ``policy``'s feature restrictions to ``report``.

    Unreadable input fails with ``MALFORMED_INPUT``. A blocking inspection
    issue makes an otherwise clean inventory ``unknown`` with
    ``INSPECTION_INCONCLUSIVE``: an inspection that could not finish never
    counts as evidence of absence.
    """
    if not report.readable:
        return PreflightDecision(
            policy_id=policy.policy_id,
            feature_support=CheckState.FAIL,
            reason_codes=(ReasonCode.MALFORMED_INPUT,),
            blocking_features=(),
            unknown_features=(),
            blocking_issues=(),
        )
    evaluation = policy.evaluate_features(report.features)
    state = evaluation.check_state
    reasons = list(evaluation.reason_codes)
    issues = tuple(issue for issue in report.issues if issue in BLOCKING_ISSUES)
    if issues:
        if ReasonCode.INSPECTION_INCONCLUSIVE not in reasons:
            reasons.append(ReasonCode.INSPECTION_INCONCLUSIVE)
        if state is CheckState.PASS:
            state = CheckState.UNKNOWN
    return PreflightDecision(
        policy_id=policy.policy_id,
        feature_support=state,
        reason_codes=tuple(reasons),
        blocking_features=evaluation.blocking,
        unknown_features=evaluation.unknown,
        blocking_issues=issues,
    )


# --- Detection vocabulary -------------------------------------------------------

_NAVIGATION_ACTIONS = frozenset({"/GoTo", "/Named", "/Thread"})
_EXTERNAL_ACTIONS = frozenset({"/URI", "/GoToR"})
_ACTIVE_ACTIONS = frozenset(
    {
        "/JavaScript",
        "/ECMAScript",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
        "/ResetForm",
        "/Hide",
        "/Rendition",
        "/Sound",
        "/Movie",
        "/GoTo3DView",
        "/RichMediaExecute",
        "/SetOCGState",
        "/Trans",
    }
)
_MARKUP_ANNOTATIONS = frozenset(
    {
        "/Text",
        "/FreeText",
        "/Line",
        "/Square",
        "/Circle",
        "/Polygon",
        "/PolyLine",
        "/Highlight",
        "/Underline",
        "/Squiggly",
        "/StrikeOut",
        "/Stamp",
        "/Caret",
        "/Ink",
        "/Popup",
        "/PrinterMark",
        "/TrapNet",
        "/Watermark",
        "/Redact",
        "/Projection",
    }
)
_ACTIVE_ANNOTATIONS = frozenset({"/Screen", "/Movie", "/Sound", "/RichMedia", "/3D"})

# Features whose indicators the object sweep and raw scan look for. These are
# the features where a parser disagreement or a hidden object matters most.
SWEPT_FEATURES = (
    Feature.DIGITAL_SIGNATURE,
    Feature.ACTIVE_CONTENT,
    Feature.EMBEDDED_FILES,
    Feature.XFA_FORM,
    Feature.OPTIONAL_CONTENT,
)
# Page-level features that cannot be ruled out for pages that were not walked.
_PAGE_FEATURES = (
    Feature.ANNOTATIONS,
    Feature.LINKS,
    Feature.INTERACTIVE_FORM,
    Feature.DIGITAL_SIGNATURE,
    Feature.ACTIVE_CONTENT,
    Feature.EMBEDDED_FILES,
)

# PDF name tokens outside stream data. A token must end at a PDF delimiter or
# whitespace, so /JS does not match /JSFoo. Hex-escaped names (/Java#53cript)
# are not matched here; the object sweep sees them normalized.
_RAW_TOKEN = re.compile(
    rb"/(ByteRange|DocTimeStamp|JavaScript|JS|Launch|RichMedia|EmbeddedFiles|"
    rb"EmbeddedFile|XFA|OCProperties|Encrypt)(?=[\x00\t\n\x0c\r ()<>\[\]{}/%]|\Z)"
)
_RAW_TOKEN_FEATURES = {
    b"ByteRange": Feature.DIGITAL_SIGNATURE,
    b"DocTimeStamp": Feature.DIGITAL_SIGNATURE,
    b"JavaScript": Feature.ACTIVE_CONTENT,
    b"JS": Feature.ACTIVE_CONTENT,
    b"Launch": Feature.ACTIVE_CONTENT,
    b"RichMedia": Feature.ACTIVE_CONTENT,
    b"EmbeddedFiles": Feature.EMBEDDED_FILES,
    b"EmbeddedFile": Feature.EMBEDDED_FILES,
    b"XFA": Feature.XFA_FORM,
    b"OCProperties": Feature.OPTIONAL_CONTENT,
    b"Encrypt": Feature.ENCRYPTION,
}


# --- Inventory accumulation -------------------------------------------------------


class _LimitReached(Exception):
    pass


class _Inventory:
    def __init__(self) -> None:
        self._present: dict[Feature, list[str]] = {}
        self._unknown: dict[Feature, list[str]] = {}
        self.issues: list[InspectionIssue] = []

    def present(self, feature: Feature, basis: str) -> None:
        bases = self._present.setdefault(feature, [])
        if basis not in bases:
            bases.append(basis)

    def unknown(self, feature: Feature, basis: str) -> None:
        bases = self._unknown.setdefault(feature, [])
        if basis not in bases:
            bases.append(basis)

    def is_present(self, feature: Feature) -> bool:
        return feature in self._present

    def issue(self, issue: InspectionIssue) -> None:
        if issue not in self.issues:
            self.issues.append(issue)

    def result(
        self,
    ) -> tuple[Mapping[Feature, FeaturePresence], Mapping[Feature, tuple[str, ...]]]:
        features: dict[Feature, FeaturePresence] = {}
        evidence: dict[Feature, tuple[str, ...]] = {}
        for feature in Feature:
            if feature in self._present:
                features[feature] = FeaturePresence.PRESENT
                evidence[feature] = tuple(self._present[feature])
            elif feature in self._unknown:
                features[feature] = FeaturePresence.UNKNOWN
                evidence[feature] = tuple(self._unknown[feature])
            else:
                features[feature] = FeaturePresence.ABSENT
        return MappingProxyType(features), MappingProxyType(evidence)


def _resolve(value):
    """Return ``(object, key)``; ``key`` identifies an indirect object, else ``None``.

    A null object, or a reference to a missing object, resolves to ``None``:
    the PDF specification treats both as an absent entry.
    """
    key = None
    if isinstance(value, IndirectObject):
        key = (value.idnum, value.generation)
        value = value.get_object()
    if isinstance(value, NullObject):
        value = None
    return value, key


def _raw(container, key: str):
    """The unresolved value at ``key`` of a dictionary, or ``None``."""
    if isinstance(container, DictionaryObject) and key in container:
        return container.raw_get(key)
    return None


def _get(container, key: str):
    """The resolved value at ``key`` of a dictionary, or ``None``."""
    return _resolve(_raw(container, key))[0]


class _Budget:
    """Counts nodes visited in one structure and detects reference cycles."""

    def __init__(self, limit: int):
        self.limit = limit
        self.count = 0
        self.seen: set[tuple[int, int]] = set()

    def visit(self, key: tuple[int, int] | None) -> bool:
        """Return False if ``key`` was already visited; raise past the limit."""
        if key is not None:
            if key in self.seen:
                return False
            self.seen.add(key)
        self.count += 1
        if self.count > self.limit:
            raise _LimitReached
        return True


# --- Structural walk ----------------------------------------------------------------


class _Walker:
    def __init__(self, reader, inventory: _Inventory, limits: InspectionLimits):
        self.reader = reader
        self.inv = inventory
        self.limits = limits

    def guarded(self, features: tuple[Feature, ...], basis: str, detector, *args) -> None:
        """Run a detector; any failure makes its features unknown."""
        try:
            detector(*args)
        except _LimitReached:
            self.inv.issue(InspectionIssue.LIMIT_REACHED)
            for feature in features:
                self.inv.unknown(feature, basis + ".limit")
        except Exception:
            for feature in features:
                self.inv.unknown(feature, basis + ".error")

    # Actions --------------------------------------------------------------------

    def actions(self, raw_action, *, automatic: bool, basis: str) -> None:
        """Classify an action and its ``/Next`` chain.

        ``automatic`` actions run without a click (open and additional
        actions), so external navigation from them counts as active content.
        """
        budget = _Budget(self.limits.max_tree_nodes)
        stack = [raw_action]
        while stack:
            action, key = _resolve(stack.pop())
            if action is None or not budget.visit(key):
                continue
            if not isinstance(action, DictionaryObject):
                self.inv.unknown(Feature.ACTIVE_CONTENT, basis + ".malformed")
                continue
            kind = _get(action, "/S")
            if "/JS" in action:
                self.inv.present(Feature.ACTIVE_CONTENT, basis + ".javascript")
            if kind in _ACTIVE_ACTIONS:
                self.inv.present(Feature.ACTIVE_CONTENT, basis)
            elif kind in _EXTERNAL_ACTIONS:
                self.inv.present(Feature.LINKS, basis)
                if automatic:
                    self.inv.present(Feature.ACTIVE_CONTENT, basis + ".automatic_navigation")
            elif kind == "/GoToE":
                self.inv.present(Feature.EMBEDDED_FILES, basis)
                if automatic:
                    self.inv.present(Feature.ACTIVE_CONTENT, basis + ".automatic_navigation")
            elif kind not in _NAVIGATION_ACTIONS:
                self.inv.unknown(Feature.ACTIVE_CONTENT, basis + ".unrecognized")
            following = _raw(action, "/Next")
            if following is None:
                continue
            chain = _resolve(following)[0]
            if isinstance(chain, ArrayObject):
                stack.extend(reversed(chain))
            else:
                stack.append(following)

    # Catalog --------------------------------------------------------------------

    def catalog(self, root) -> None:
        g = self.guarded
        g((Feature.DIGITAL_SIGNATURE,), "catalog.signatures", self._catalog_signatures, root)
        # Features an action can reveal (see ``actions``).
        acting = (Feature.ACTIVE_CONTENT, Feature.LINKS, Feature.EMBEDDED_FILES)
        g(
            (Feature.INTERACTIVE_FORM, Feature.XFA_FORM, Feature.DIGITAL_SIGNATURE, *acting),
            "acroform",
            self._acroform,
            root,
        )
        g((Feature.ACTIVE_CONTENT, Feature.EMBEDDED_FILES), "catalog.names", self._names, root)
        g(acting, "catalog.actions", self._catalog_actions, root)
        g((Feature.OUTLINES, *acting), "outlines", self._outlines, root)
        g((Feature.TAGGED_STRUCTURE,), "catalog.tags", self._tags, root)
        g((Feature.EMBEDDED_FILES,), "catalog.embedded", self._catalog_embedded, root)
        g((Feature.OPTIONAL_CONTENT,), "catalog.optional_content", self._optional_content, root)

    def _catalog_signatures(self, root) -> None:
        if _get(root, "/Perms") is not None:
            self.inv.present(Feature.DIGITAL_SIGNATURE, "catalog.perms")
        if _get(root, "/DSS") is not None:
            self.inv.present(Feature.DIGITAL_SIGNATURE, "catalog.dss")

    def _acroform(self, root) -> None:
        acroform = _get(root, "/AcroForm")
        if acroform is None:
            return
        if not isinstance(acroform, DictionaryObject):
            for feature in (Feature.INTERACTIVE_FORM, Feature.XFA_FORM, Feature.DIGITAL_SIGNATURE):
                self.inv.unknown(feature, "acroform.malformed")
            return
        if _get(acroform, "/XFA") is not None:
            self.inv.present(Feature.XFA_FORM, "acroform.xfa")
            self.inv.present(Feature.INTERACTIVE_FORM, "acroform.xfa")
        fields = _get(acroform, "/Fields")
        if fields is not None and not isinstance(fields, ArrayObject):
            self.inv.unknown(Feature.INTERACTIVE_FORM, "acroform.fields_malformed")
            self.inv.unknown(Feature.DIGITAL_SIGNATURE, "acroform.fields_malformed")
            return
        signed = self._fields(list(fields or ()))
        flags = _get(acroform, "/SigFlags")
        append_only = isinstance(flags, int) and bool(int(flags) & 2)
        if append_only and not signed:
            # Signers set AppendOnly; with no signature found, one may be hidden.
            self.inv.unknown(Feature.DIGITAL_SIGNATURE, "acroform.append_only_unsigned")

    def _fields(self, roots: list) -> bool:
        """Walk the field tree; return True if a signed signature field exists."""
        budget = _Budget(self.limits.max_tree_nodes)
        signed = False
        stack = [(item, None) for item in reversed(roots)]
        while stack:
            raw_node, inherited_type = stack.pop()
            node, key = _resolve(raw_node)
            if node is None or not budget.visit(key):
                continue
            if not isinstance(node, DictionaryObject):
                self.inv.unknown(Feature.INTERACTIVE_FORM, "acroform.field_malformed")
                continue
            self.inv.present(Feature.INTERACTIVE_FORM, "acroform.field")
            field_type = _get(node, "/FT") or inherited_type
            if field_type == "/Sig" and isinstance(_get(node, "/V"), DictionaryObject):
                self.inv.present(Feature.DIGITAL_SIGNATURE, "acroform.signed_field")
                signed = True
            if _get(node, "/AA") is not None:
                self.inv.present(Feature.ACTIVE_CONTENT, "acroform.field_additional_actions")
            if _raw(node, "/A") is not None:
                self.actions(_raw(node, "/A"), automatic=False, basis="acroform.field_action")
            kids = _get(node, "/Kids")
            if isinstance(kids, ArrayObject):
                stack.extend((kid, field_type) for kid in reversed(kids))
            elif kids is not None:
                self.inv.unknown(Feature.INTERACTIVE_FORM, "acroform.kids_malformed")
        return signed

    def _names(self, root) -> None:
        names = _get(root, "/Names")
        if names is None:
            return
        if not isinstance(names, DictionaryObject):
            self.inv.unknown(Feature.ACTIVE_CONTENT, "catalog.names_malformed")
            self.inv.unknown(Feature.EMBEDDED_FILES, "catalog.names_malformed")
            return
        for key, feature, basis in (
            ("/JavaScript", Feature.ACTIVE_CONTENT, "names.javascript"),
            ("/EmbeddedFiles", Feature.EMBEDDED_FILES, "names.embedded_files"),
            ("/Renditions", Feature.ACTIVE_CONTENT, "names.renditions"),
            ("/AlternatePresentations", Feature.ACTIVE_CONTENT, "names.alternate_presentations"),
        ):
            tree = _get(names, key)
            if tree is None:
                continue
            occupied = _name_tree_occupied(tree)
            if occupied is None:
                self.inv.unknown(feature, basis + ".malformed")
            elif occupied:
                self.inv.present(feature, basis)

    def _catalog_actions(self, root) -> None:
        if _get(root, "/AA") is not None:
            self.inv.present(Feature.ACTIVE_CONTENT, "catalog.additional_actions")
        opener = _raw(root, "/OpenAction")
        resolved = _resolve(opener)[0] if opener is not None else None
        if resolved is None or isinstance(resolved, ArrayObject):
            return  # No open action, or a plain destination.
        self.actions(opener, automatic=True, basis="catalog.open_action")

    def _outlines(self, root) -> None:
        outlines = _get(root, "/Outlines")
        if outlines is None:
            return
        if not isinstance(outlines, DictionaryObject):
            self.inv.unknown(Feature.OUTLINES, "outlines.malformed")
            return
        first = _raw(outlines, "/First")
        if first is None:
            return
        self.inv.present(Feature.OUTLINES, "outlines.items")
        budget = _Budget(self.limits.max_tree_nodes)
        stack = [first]
        while stack:
            item, key = _resolve(stack.pop())
            if item is None or not budget.visit(key):
                continue
            if not isinstance(item, DictionaryObject):
                self.inv.unknown(Feature.ACTIVE_CONTENT, "outlines.item_malformed")
                continue
            if _raw(item, "/A") is not None:
                self.actions(_raw(item, "/A"), automatic=False, basis="outlines.action")
            for link in ("/Next", "/First"):
                if _raw(item, link) is not None:
                    stack.append(_raw(item, link))

    def _tags(self, root) -> None:
        if _get(root, "/StructTreeRoot") is not None:
            self.inv.present(Feature.TAGGED_STRUCTURE, "catalog.struct_tree_root")
        mark_info = _get(root, "/MarkInfo")
        marked = _get(mark_info, "/Marked") if isinstance(mark_info, DictionaryObject) else None
        if marked is not None and marked == True:  # noqa: E712 - pypdf BooleanObject
            self.inv.present(Feature.TAGGED_STRUCTURE, "catalog.mark_info")

    def _catalog_embedded(self, root) -> None:
        if _get(root, "/Collection") is not None:
            self.inv.present(Feature.EMBEDDED_FILES, "catalog.collection")
        if _get(root, "/AF") is not None:
            self.inv.present(Feature.EMBEDDED_FILES, "catalog.associated_files")

    def _optional_content(self, root) -> None:
        if _get(root, "/OCProperties") is not None:
            self.inv.present(Feature.OPTIONAL_CONTENT, "catalog.oc_properties")

    # Pages ------------------------------------------------------------------------

    def pages(self, pages) -> list[PageGeometry | None]:
        geometries: list[PageGeometry | None] = []
        for index, page in enumerate(pages):
            if index >= self.limits.max_pages:
                self.inv.issue(InspectionIssue.LIMIT_REACHED)
                for feature in _PAGE_FEATURES:
                    self.inv.unknown(feature, "pages.limit")
                break
            geometries.append(_geometry(page))
            self.guarded(_PAGE_FEATURES, "page", self._page, page)
        return geometries

    def _page(self, page) -> None:
        if _get(page, "/AA") is not None:
            self.inv.present(Feature.ACTIVE_CONTENT, "page.additional_actions")
        if _get(page, "/AF") is not None:
            self.inv.present(Feature.EMBEDDED_FILES, "page.associated_files")
        annotations = _get(page, "/Annots")
        if annotations is None:
            return
        if not isinstance(annotations, ArrayObject):
            for feature in (Feature.ANNOTATIONS, Feature.LINKS, Feature.INTERACTIVE_FORM):
                self.inv.unknown(feature, "page.annotations_malformed")
            return
        for raw_annotation in annotations:
            annotation = _resolve(raw_annotation)[0]
            if annotation is None:
                continue
            if not isinstance(annotation, DictionaryObject):
                self.inv.unknown(Feature.ANNOTATIONS, "annotation.malformed")
                continue
            self._annotation(annotation)

    def _annotation(self, annotation) -> None:
        subtype = _get(annotation, "/Subtype")
        if subtype == "/Link":
            self.inv.present(Feature.LINKS, "annotation.link")
        elif subtype == "/Widget":
            self.inv.present(Feature.INTERACTIVE_FORM, "annotation.widget")
            if _signed_widget(annotation):
                self.inv.present(Feature.DIGITAL_SIGNATURE, "annotation.signed_widget")
        elif subtype == "/FileAttachment":
            self.inv.present(Feature.ANNOTATIONS, "annotation.file_attachment")
            self.inv.present(Feature.EMBEDDED_FILES, "annotation.file_attachment")
        elif subtype in _ACTIVE_ANNOTATIONS:
            self.inv.present(Feature.ANNOTATIONS, "annotation.multimedia")
            self.inv.present(Feature.ACTIVE_CONTENT, "annotation.multimedia")
        elif subtype in _MARKUP_ANNOTATIONS:
            self.inv.present(Feature.ANNOTATIONS, "annotation.markup")
        else:
            self.inv.present(Feature.ANNOTATIONS, "annotation.unrecognized")
            self.inv.unknown(Feature.ACTIVE_CONTENT, "annotation.unrecognized")
        if _raw(annotation, "/A") is not None:
            self.actions(_raw(annotation, "/A"), automatic=False, basis="annotation.action")
        if _get(annotation, "/AA") is not None:
            self.inv.present(Feature.ACTIVE_CONTENT, "annotation.additional_actions")


def _name_tree_occupied(node) -> bool | None:
    """True if a name tree has entries or children, None if it is malformed."""
    if not isinstance(node, DictionaryObject):
        return None
    for key in ("/Names", "/Kids"):
        value = _get(node, key)
        if value is None:
            continue
        if not isinstance(value, ArrayObject):
            return None
        if len(value):
            return True
    return False


_MAX_FIELD_DEPTH = 64


def _signed_widget(widget) -> bool:
    """A widget whose field (itself or an ancestor) is a signed signature field.

    Raises :class:`_LimitReached` for a parent chain deeper than any real
    field hierarchy.
    """
    field_type = value = None
    raw_node, seen = widget, set()
    for _ in range(_MAX_FIELD_DEPTH):
        node, key = _resolve(raw_node)
        if not isinstance(node, DictionaryObject) or (key is not None and key in seen):
            return field_type == "/Sig" and isinstance(value, DictionaryObject)
        if key is not None:
            seen.add(key)
        field_type = field_type or _get(node, "/FT")
        value = value if value is not None else _get(node, "/V")
        raw_node = _raw(node, "/Parent")
    raise _LimitReached


def _box(value) -> tuple[float, float, float, float]:
    return tuple(float(number) for number in value)  # type: ignore[return-value]


def _geometry(page) -> PageGeometry | None:
    try:
        return PageGeometry(
            media_box=_box(page.mediabox),
            crop_box=_box(page.cropbox),
            rotation=int(page.rotation),
            user_unit=float(page.user_unit),
        )
    except Exception:
        return None


# --- Object sweep and raw scan ------------------------------------------------------


def _object_indicators(obj) -> set[Feature]:
    found: set[Feature] = set()
    object_type = _get(obj, "/Type")
    if object_type in ("/Sig", "/DocTimeStamp") or "/ByteRange" in obj:
        found.add(Feature.DIGITAL_SIGNATURE)
    if (
        "/JS" in obj
        or _get(obj, "/S") in _ACTIVE_ACTIONS
        or _get(obj, "/Subtype") in _ACTIVE_ANNOTATIONS
    ):
        found.add(Feature.ACTIVE_CONTENT)
    if object_type == "/EmbeddedFile" or "/EF" in obj:
        found.add(Feature.EMBEDDED_FILES)
    if "/XFA" in obj:
        found.add(Feature.XFA_FORM)
    if object_type in ("/OCG", "/OCMD"):
        found.add(Feature.OPTIONAL_CONTENT)
    return found


def _collect_names(obj, seen: set[str], depth: int = 0) -> None:
    """Add every name (dictionary key or name value) in ``obj`` to ``seen``,
    descending into direct dictionaries and arrays but not references."""
    if depth > 64:
        return
    if isinstance(obj, DictionaryObject):
        for key in obj:
            seen.add(str(key))
            _collect_names(obj.raw_get(key), seen, depth + 1)
    elif isinstance(obj, ArrayObject):
        for item in obj:
            _collect_names(item, seen, depth + 1)
    elif isinstance(obj, NameObject):
        seen.add(str(obj))


def _sweep(
    reader, inventory: _Inventory, limits: InspectionLimits
) -> tuple[set[Feature], set[str]]:
    """Indicators in every indirect object the cross-reference table lists.

    Returns the features indicated and every name the parsed objects and the
    trailer contain, so the raw scan can tell parsed tokens from unparsed ones.
    """
    keys: dict[tuple[int, int], None] = {}
    for generation, table in reader.xref.items():
        for number in table:
            keys[(number, generation)] = None
    for number in reader.xref_objStm:
        keys[(number, 0)] = None
    if len(keys) > limits.max_objects:
        inventory.issue(InspectionIssue.LIMIT_REACHED)
        for feature in SWEPT_FEATURES:
            inventory.unknown(feature, "sweep.limit")
    found: set[Feature] = set()
    names: set[str] = set()
    _collect_names(reader.trailer, names)
    unreadable = 0
    for number, generation in list(keys)[: limits.max_objects]:
        try:
            obj = reader.get_object(IndirectObject(number, generation, reader))
            if isinstance(obj, DictionaryObject):
                found |= _object_indicators(obj)
            _collect_names(obj, names)
        except Exception:
            unreadable += 1
    if unreadable:
        inventory.issue(InspectionIssue.OBJECTS_UNREADABLE)
        for feature in SWEPT_FEATURES:
            inventory.unknown(feature, "sweep.unreadable_objects")
    return found, names


def _structure_spans(data: bytes) -> Iterator[tuple[int, int]]:
    """Byte ranges outside stream data, in one linear pass.

    Stream data is binary or page content, so tokens inside it are not
    structure. A ``stream`` keyword without a following ``endstream`` leaves
    the rest of the file in scope, which can only add findings.
    """
    position = 0
    while True:
        keyword = data.find(b"stream", position)
        if keyword < 0:
            break
        body = keyword + 6
        if data[body : body + 2] == b"\r\n":
            body += 2
        elif data[body : body + 1] in (b"\n", b"\r"):
            body += 1
        else:
            body = -1  # "streams", "/streamfoo": not the keyword.
        if body < 0 or data[max(0, keyword - 3) : keyword] == b"end":
            yield position, keyword + 6
            position = keyword + 6
            continue
        end = data.find(b"endstream", body)
        if end < 0:
            break
        yield position, body
        position = end + 9
    yield position, len(data)


def _raw_scan(data: bytes, parsed_names: set[str]) -> set[Feature]:
    """Features whose name tokens appear in the file outside stream data but
    in no object the parser read: in bytes outside the cross-reference table,
    a damaged region, or a superseded revision."""
    found: set[Feature] = set()
    for start, end in _structure_spans(data):
        for match in _RAW_TOKEN.finditer(data, start, end):
            token = match.group(1)
            if "/" + token.decode("ascii") not in parsed_names:
                found.add(_RAW_TOKEN_FEATURES[token])
    return found


# --- Page classification -------------------------------------------------------------


def _char_counts(textpage) -> tuple[int, int]:
    """Return (usable, unmappable) characters, ignoring generated ones."""
    usable = unmappable = 0
    handle = textpage.raw
    for index in range(textpage.count_chars()):
        if pdfium_c.FPDFText_IsGenerated(handle, index) == 1:
            continue
        code = pdfium_c.FPDFText_GetUnicode(handle, index)
        if code == 0 or code == 0xFFFD or code > 0x10FFFF:
            unmappable += 1
            continue
        char = chr(code)
        if char.isspace():
            continue
        category = unicodedata.category(char)
        if category == "Cf":
            continue  # Soft hyphens and zero-width marks carry no text.
        if category in ("Cc", "Cs", "Co", "Cn"):
            unmappable += 1
        else:
            usable += 1
    return usable, unmappable


_MAX_BLANK_RENDER_PIXELS = 4_000_000


def _visibly_blank(page, dpi: int) -> bool | None:
    try:
        width, height = page.get_size()
        scale = dpi / 72
        if width * height * scale * scale > _MAX_BLANK_RENDER_PIXELS:
            scale = (_MAX_BLANK_RENDER_PIXELS / (width * height)) ** 0.5
        bitmap = page.render(
            scale=scale,
            draw_annots=True,
            fill_color=(255, 255, 255, 255),
            grayscale=True,
        )
        try:
            image = bitmap.to_pil().convert("L")
        finally:
            bitmap.close()
        low, _high = ImageStat.Stat(image).extrema[0]
        return low >= 250
    except Exception:
        return None


def _classify_page(
    page, index: int, geometry: PageGeometry | None, limits: InspectionLimits
) -> PageInspection:
    text_objects = image_objects = vector_objects = 0
    for obj in page.get_objects(max_depth=limits.max_form_depth):
        if obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
            text_objects += 1
        elif obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            image_objects += 1
        elif obj.type in (pdfium_c.FPDF_PAGEOBJ_PATH, pdfium_c.FPDF_PAGEOBJ_SHADING):
            vector_objects += 1
    textpage = page.get_textpage()
    try:
        usable, unmappable = _char_counts(textpage)
    finally:
        textpage.close()

    if usable:
        kind = PageKind.MIXED if image_objects else PageKind.TEXT
    elif unmappable:
        kind = PageKind.UNKNOWN
    elif image_objects:
        kind = PageKind.IMAGE_ONLY
    else:
        blank = _visibly_blank(page, limits.blank_render_dpi)
        if blank is True:
            kind = PageKind.BLANK
        elif blank is False and vector_objects:
            kind = PageKind.GRAPHICS_ONLY
        else:
            # Something visible that no recognized object explains, or the
            # render failed.
            kind = PageKind.UNKNOWN
    return PageInspection(
        index=index,
        kind=kind,
        geometry=geometry,
        text_chars=usable,
        unmappable_chars=unmappable,
        text_objects=text_objects,
        image_objects=image_objects,
        vector_objects=vector_objects,
    )


def _classify_pages(
    data: bytes,
    geometries: list[PageGeometry | None],
    page_count: int,
    inventory: _Inventory,
    limits: InspectionLimits,
) -> tuple[PageInspection, ...]:
    def geometry(index: int) -> PageGeometry | None:
        return geometries[index] if index < len(geometries) else None

    def unknown_pages() -> tuple[PageInspection, ...]:
        return tuple(
            PageInspection(index, PageKind.UNKNOWN, geometry(index)) for index in range(page_count)
        )

    try:
        document = pdfium.PdfDocument(data)
    except Exception:
        inventory.issue(InspectionIssue.RENDERER_UNREADABLE)
        return unknown_pages()
    try:
        if len(document) != page_count:
            inventory.issue(InspectionIssue.PAGE_COUNT_MISMATCH)
            return unknown_pages()
        pages: list[PageInspection] = []
        for index in range(page_count):
            unknown = PageInspection(index, PageKind.UNKNOWN, geometry(index))
            if index >= limits.max_pages:
                inventory.issue(InspectionIssue.LIMIT_REACHED)
                pages.append(unknown)
                continue
            try:
                page = document[index]
            except Exception:
                pages.append(unknown)
                continue
            try:
                pages.append(_classify_page(page, index, geometry(index), limits))
            except Exception:
                pages.append(unknown)
            finally:
                page.close()
        return tuple(pages)
    finally:
        document.close()


# --- Entry point ----------------------------------------------------------------------


class _WarningCounter(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


@contextmanager
def _pypdf_warnings() -> Iterator[_WarningCounter]:
    """Count pypdf's warnings instead of letting them reach stderr.

    pypdf reports recovered damage through logging. Only the count is kept:
    messages can name document objects.
    """
    logger = logging.getLogger("pypdf")
    counter = _WarningCounter()
    previous_level = logger.level
    previous_propagate = logger.propagate
    logger.addHandler(counter)
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    try:
        yield counter
    finally:
        logger.removeHandler(counter)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def _require_dependencies() -> None:
    if PdfReader is None:
        raise CompressionError(
            "pypdf is required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )
    if pdfium is None or ImageStat is None:
        raise CompressionError(
            "pypdfium2 and Pillow are required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )


def _unreadable(issue: InspectionIssue) -> InspectionReport:
    features = MappingProxyType({feature: FeaturePresence.UNKNOWN for feature in Feature})
    evidence = MappingProxyType({feature: ("document." + issue.value,) for feature in Feature})
    return InspectionReport(
        readable=False,
        page_count=None,
        features=features,
        evidence=evidence,
        pages=(),
        issues=(issue,),
    )


def _decrypts_without_password(reader) -> bool:
    try:
        return bool(reader.decrypt(""))
    except Exception:
        return False


def inspect_pdf(
    source: Path | bytes, *, limits: InspectionLimits = DEFAULT_LIMITS
) -> InspectionReport:
    """Inspect a PDF file (or its bytes) without modifying it.

    Never raises for damaged or hostile input: those produce a report with
    ``readable`` false or ``unknown`` features. Raises
    :class:`CompressionError` only if a required library is missing and
    :class:`OSError` if the file cannot be read.
    """
    _require_dependencies()
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    inventory = _Inventory()

    with _pypdf_warnings() as warnings:
        try:
            reader = PdfReader(io.BytesIO(data), strict=False)
            encrypted = bool(reader.is_encrypted)
        except Exception:
            return _unreadable(InspectionIssue.UNREADABLE)

        if encrypted:
            inventory.present(Feature.ENCRYPTION, "trailer.encrypt")
            if not _decrypts_without_password(reader):
                inventory.issue(InspectionIssue.CONTENT_ENCRYPTED)
                for feature in Feature:
                    if feature is not Feature.ENCRYPTION:
                        inventory.unknown(feature, "document.content_encrypted")
                features, evidence = inventory.result()
                return InspectionReport(
                    readable=True,
                    page_count=None,
                    features=features,
                    evidence=evidence,
                    pages=(),
                    issues=tuple(inventory.issues),
                )

        try:
            root = reader.trailer["/Root"].get_object()
            pages = reader.pages
            page_count = len(pages)
        except Exception:
            return _unreadable(InspectionIssue.UNREADABLE)
        if not isinstance(root, DictionaryObject):
            return _unreadable(InspectionIssue.UNREADABLE)
        if page_count < 1:
            return _unreadable(InspectionIssue.NO_PAGES)

        walker = _Walker(reader, inventory, limits)
        walker.catalog(root)
        try:
            geometries = walker.pages(pages)
        except Exception:
            geometries = []
            for feature in _PAGE_FEATURES:
                inventory.unknown(feature, "pages.error")

        try:
            swept, parsed_names = _sweep(reader, inventory, limits)
        except Exception:
            swept, parsed_names = set(), set()
            inventory.issue(InspectionIssue.OBJECTS_UNREADABLE)
            for feature in SWEPT_FEATURES:
                inventory.unknown(feature, "sweep.error")
        recovered = warnings.count > 0

    for feature in swept:
        if not inventory.is_present(feature):
            inventory.unknown(feature, "sweep.unreferenced_indicator")
    for feature in _raw_scan(data, parsed_names):
        if not inventory.is_present(feature):
            inventory.unknown(feature, "raw.unreferenced_token")
    if recovered:
        inventory.issue(InspectionIssue.PARSER_RECOVERED)

    page_reports = _classify_pages(data, geometries, page_count, inventory, limits)
    features, evidence = inventory.result()
    return InspectionReport(
        readable=True,
        page_count=page_count,
        features=features,
        evidence=evidence,
        pages=page_reports,
        issues=tuple(inventory.issues),
    )


def pdf_page_count(path: Path) -> int:
    """Page count of a readable, unencrypted PDF (legacy helper)."""
    if PdfReader is None:
        raise CompressionError(
            "pypdf is required. Install dependencies with: "
            "python -m pip install -r requirements.txt"
        )
    try:
        with path.open("rb") as stream:
            document = PdfReader(stream, strict=False)
            if document.is_encrypted:
                raise CompressionError(
                    "This PDF is password-protected. Unlock it before compressing."
                )
            page_count = len(document.pages)
            if page_count < 1:
                raise CompressionError("The input PDF has no pages.")
            return page_count
    except CompressionError:
        raise
    except Exception as exc:
        raise CompressionError(f"Could not read input PDF: {exc}") from exc
