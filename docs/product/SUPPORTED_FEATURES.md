# Supported-feature matrix

Status: implemented by CHONK-005 (preflight inspection) for inspector version 1. The matrix is enforced in code. `tests/unit/test_supported_features_doc.py` fails if these tables and the policy definitions in [`src/chonk/policies.py`](../../src/chonk/policies.py) disagree.

CHONK inspects every source PDF before any backend runs ([`src/chonk/inspection.py`](../../src/chonk/inspection.py)). The engine applies the request's policy to the inspection report. If the `feature_support` check is anything other than `pass`, the result is `blocked`: Ghostscript is never discovered or run, and nothing is written. The original file is only read.

References: [requirements](PRODUCT_REQUIREMENTS.md) FR-03 and FR-04, [architecture](ARCHITECTURE.md) section 4, [validation matrix](VALIDATION.md) "Feature inventory".

## 1. Document features

Every feature is reported as `present`, `absent`, or `unknown`. CHONK reports `absent` only when every applicable detector finished and found nothing. `unknown` always blocks with `INSPECTION_INCONCLUSIVE`: CHONK never treats an inspection it could not finish as proof that a feature is absent.

"Blocked" means the feature's presence stops processing. A feature stays blocked until a validator can prove that compression preserves it (CHONK-008), and a new policy version then allows it. No v1 policy allows any listed feature.

| Feature | Detected from | `preserve-existing-text-v1` | `require-searchable-v1` | `scan-review-v1` | Why v1 blocks it |
|---|---|---|---|---|---|
| `encryption` | Trailer `/Encrypt` | Blocked | Blocked | Blocked | `ENCRYPTED_INPUT`. A rewrite would drop or change protection; decryption is deferred (CHONK-029). |
| `digital_signature` | A signature field with a value; a signed widget; catalog `/Perms` or `/DSS` | Blocked | Blocked | Blocked | `SIGNED_INPUT`. Any rewrite invalidates the signature. |
| `interactive_form` | Any AcroForm field, including signature fields; any widget annotation | Blocked | Blocked | Blocked | Ghostscript drops the AcroForm and flattens field values (CHONK-001 baseline). |
| `xfa_form` | AcroForm `/XFA` | Blocked | Blocked | Blocked | Ghostscript does not carry XFA. |
| `active_content` | JavaScript name tree; script, launch, submit, import, reset, hide, multimedia, 3D, layer-state, and transition actions anywhere; catalog, page, field, and annotation additional actions; external navigation from an open or additional action; multimedia annotations | Blocked | Blocked | Blocked | Scripts and actions are neither preserved nor validated, and document content must never drive behavior. |
| `embedded_files` | EmbeddedFiles name tree, file-attachment annotations, `/Collection` portfolios, associated files (`/AF`), embedded go-to actions | Blocked | Blocked | Blocked | Attachments are not inspected or validated, and they can carry undisclosed data. |
| `annotations` | Any annotation other than links and widgets | Blocked | Blocked | Blocked | Annotation preservation has no validator yet. |
| `links` | Link annotations; URI and remote go-to actions | Blocked | Blocked | Blocked | Link preservation has no validator yet. |
| `outlines` | Catalog `/Outlines` with at least one item | Blocked | Blocked | Blocked | Outline preservation has no validator yet. |
| `tagged_structure` | Catalog `/StructTreeRoot`, or `/MarkInfo /Marked true` | Blocked | Blocked | Blocked | FR-04: tagged documents are blocked until accessibility structure can be preserved and checked. |
| `optional_content` | Catalog `/OCProperties` | Blocked | Blocked | Blocked | Hidden layers can hold content that the default view, and so visual comparison, never shows. |

Some constructs are recorded but not treated as features. An open action that is a plain destination only opens the document at a page, so it is not active content. Metadata, page labels, named destinations, article threads, and output intents are not inspected in version 1. Ghostscript may change them.

### How detection stays conservative

Three independent views are combined:

1. **Structural walk.** Starts at the document catalog and covers the form-field tree, name trees, open and additional actions (following `/Next` chains), outline items, and every page's annotations. This walk alone decides that a feature is `present`.
2. **Object sweep.** Examines every indirect object listed in the cross-reference table, whether or not anything references it. It looks for signatures, scripts and active actions, embedded files, XFA, and optional-content groups. If one appears where the walk found nothing, the feature is `unknown`. No conforming reader uses an unreferenced object, but a different parser, a repaired cross-reference table, or an earlier revision might.
3. **Raw scan.** Searches the file bytes outside stream data for the same indicators (plus `/Encrypt`). It counts a token only when no parsed object contains it. This catches objects outside the cross-reference table, damaged regions, and superseded revisions. Such a token makes the feature `unknown`.

These conditions also produce `unknown`: a malformed structure, an action or annotation type CHONK does not recognize, a detector error, an inspection limit, unparsable objects, and content that is encrypted with a user password.

## 2. Page classification

PDFium classifies each page. PDFium is the renderer that visual comparison uses. CHONK records counts of text, image, and vector objects, including objects inside nested form XObjects, and counts of usable and unmappable extracted characters. Extracted text itself is never kept.

| Page kind | Meaning |
|---|---|
| `text` | At least one usable character, no raster image. |
| `mixed` | At least one usable character and a raster image, for example a photograph, or a scan with an OCR text layer. Invisible (render mode 3) text counts as a text layer. |
| `image_only` | Raster images, no text layer. Typically a scan. |
| `graphics_only` | Vector drawing only, no text layer, for example a chart or text converted to outlines. |
| `blank` | No text layer and no image, and a 72 dpi render in which every pixel is at least 250/255 white. |
| `unknown` | Text objects yield only unmappable characters; something visible is drawn that no recognized object explains (for example an annotation appearance); classification failed; or a limit was reached. |

A usable character is a letter, digit, punctuation mark, or symbol. Whitespace, soft hyphens, and zero-width marks count as neither usable nor unmappable. Control, private-use, unassigned, and replacement characters are unmappable.

Inspection reports page kinds and the geometry of each page: media box, crop box, rotation, and user unit.

Page kinds decide the `text_layer` check. `blank` pages carry no content and are exempt. The check passes only when at least one page is `text` or `mixed` and every other page is `blank`:

| Pages | `text_layer` |
|---|---|
| Any `image_only` or `graphics_only` page | `fail`, naming those pages |
| No `text` or `mixed` page at all, for example an all-blank document | `fail` |
| Otherwise, any `unknown` page | `unknown`, naming those pages |
| Otherwise | `pass` |

A policy's `image_only_pages` setting decides what the check does:

- **`block`** (`require-searchable-v1`): `text_layer` is a hard check, applied at preflight before any backend runs and whatever the file's size. `fail` blocks with `TEXT_LAYER_MISSING`. `unknown` blocks with `VALIDATION_INCONCLUSIVE` if nothing else already blocks the file. CHONK does not add OCR.
- **`review`** (`preserve-existing-text-v1`, `scan-review-v1`): page kinds never block. For a rewritten output, review is decided by the preservation and visual validators (CHONK-008, CHONK-009).

A supported file that already fits the ceiling is published unchanged. Its text-preservation check is `not_applicable` only when every page is `image_only`, `graphics_only`, or `blank`; otherwise it passes, because the bytes are identical.

## 3. Inspection issues

| Issue | Blocks by itself | Meaning |
|---|---|---|
| `unreadable` | Yes (`MALFORMED_INPUT`) | The file could not be parsed as a PDF. |
| `no_pages` | Yes (`MALFORMED_INPUT`) | The PDF has no pages. |
| `content_encrypted` | Through `unknown` features | A password is needed; only encryption was inspected. |
| `parser_recovered` | No | The parser worked around damage, such as a broken cross-reference table; every detector still ran. |
| `objects_unreadable` | Through `unknown` features | Some objects could not be parsed, so the sweep is incomplete. |
| `limit_reached` | Through `unknown` features | An inspection limit stopped a detector. |
| `renderer_unreadable` | Yes (`INSPECTION_INCONCLUSIVE`) | PDFium could not open the file: pages cannot be classified or compared. |
| `page_count_mismatch` | Yes (`INSPECTION_INCONCLUSIVE`) | The structural parser and the renderer disagree about the page count. |

Default limits: 10,000 pages, 1,000,000 swept objects, 100,000 nodes in any one form-field, outline, or action structure, and a form-XObject nesting depth of 16. Reaching a limit produces `unknown`, never `absent`. These limits bound the work inspection does. They do not bound time or memory; process isolation, deadlines, and memory caps are CHONK-011.

## 4. Known limitations

- **Extraction does not prove glyph correctness.** When a font has no Unicode mapping, PDFium falls back to character codes. A Type 3 font that draws boxes for codes 65–67 extracts as `ABC`. CHONK can flag only codes that land on unusable characters. Glyph-level checks belong to visual validation (CHONK-009).
- **The raw scan has blind spots.** It cannot see names inside compressed object streams or hex-escaped names, although the sweep sees both once they are parsed. It also stays silent about a token that a parsed object legitimately contains, even if an unparsed copy exists elsewhere. A `stream` keyword inside a string literal hides the bytes up to the next `endstream` from it.
- **Null means absent.** Following the PDF specification, a `null` entry or a reference to a missing object counts as an absent entry.
- **Parser disagreement is checked only at the page count.** CHONK compares its parser (pypdf) and its renderer (PDFium) only on the number of pages. Ghostscript is a third parser whose view is not compared before it runs. Validating the output (CHONK-008 and CHONK-009) is what catches its differences.
- **Blank detection is visual.** A page whose only content is lighter than 250/255 at 72 dpi is `blank`.
- **Inspection is not sanitization.** It does not detect malware, exploit payloads, or hostile parser input, and it does not make a file safe to open. Inspection runs in-process with the same privileges as CHONK until CHONK-011 isolates it.
- **Counts only.** Evidence is a fixed code per detector finding, such as `acroform.signed_field` or `sweep.unreferenced_indicator`. Reports contain no text, names, metadata, or values from the document.
