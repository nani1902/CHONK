# CHONK tests

Baseline tests and synthetic PDF corpus (CHONK-001). They capture what the
current `pdf_compressor.py` does, protect the guarantees the migration must
keep, and pin down the known defects that later tasks must fix.

## Running

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Ghostscript is needed only for `tests/integration`. Without it those tests
are skipped and everything else still runs. The session header prints the
Ghostscript version in use. `openssl` and `git` enable one extra check each
and are skipped when missing.

Checked on Linux with Ghostscript 10.02.1: on Python 3.10 and 3.11 with
current dependencies, and on Python 3.11 with the lowest versions
`requirements.txt` and `requirements-dev.txt` allow, except that pypdfium2
must be 5.0 or newer (see the findings below). The suite takes about 15
seconds.

## Layout

| Path | Purpose |
|---|---|
| `corpus/` | Deterministic fixture generators, the fixture catalog, and a disposable test signer |
| `evaluation.py` | Independent output checks (the test oracle); it deliberately does not reuse product validation |
| `damage.py` | Deliberate output damage used to prove that the checks detect what they claim |
| `gaps.py` | The `known_gap` marker |
| `unit/` | Size parsing, profile ladder, scripted search, corpus, oracle, product checks, CLI refusals (no Ghostscript) |
| `integration/` | The CLI end to end with real Ghostscript |

## Corpus

Fixtures are generated at test time and never committed. A test fails if
a PDF, image, or key file is ever tracked by Git. Every visible text page
carries the word `SYNTHETIC`, and all names, amounts, and images are
invented. `corpus/catalog.py` records each fixture's intent, its
[validation matrix](../docs/product/VALIDATION.md) family, its features, and
the baseline behavior it captures.

To inspect the corpus, write it out with a provenance manifest:

```bash
PYTHONPATH=tests python -m corpus /tmp/chonk-corpus
```

| Fixture | What it exercises |
|---|---|
| `text-statement` | Text page, three standard fonts, numeric table |
| `text-multipage` | 12 distinct pages; Letter, A4, landscape, `/Rotate 90`; outline |
| `text-tiny` | 3.5–6 pt digits, hairlines, barcode-like bars |
| `image-scan` | Image-only pages, no text layer |
| `image-mixed` | Text layer plus an RGB photograph |
| `already-small` | Far below any realistic ceiling |
| `encrypted-user-password`, `encrypted-owner-only` | RC4-128, with and without a user password |
| `form-acroform` | Filled text field and checkbox with appearance streams |
| `annotations` | Link, note, and highlight annotations |
| `signed-pkcs7` | `adbe.pkcs7.detached` signature that verifies with OpenSSL |
| `malformed-truncated`, `malformed-bad-xref`, `malformed-not-pdf` | Unrecoverable, repairable, and non-PDF input |

Fixtures are byte-for-byte reproducible for a given set of library
versions. This is tested across interpreters and hash seeds. The signing
key is derived from a fixed seed at run time, so no private key is stored.
The key and its certificate are for these tests only.

## Known gaps

A test marked `@known_gap("CHONK-0xx", ...)` states behavior the product
specification requires but the baseline lacks. The marker is a strict
`xfail` that accepts only an `AssertionError`, so the test must fail on the
assertion that describes the gap. The change that closes the gap will make
the test pass unexpectedly, which fails the run. That change must also
remove the marker.

Baseline findings recorded this way:

| Owner | Finding |
|---|---|
| CHONK-004 | `parse_size` uses float arithmetic, so `4.1MB`, `8.2MB`, `2.05MB`, and similar inputs become one byte less than stated |
| CHONK-005 | Signed and form inputs reach Ghostscript without inspection |
| CHONK-005 / 007 | A signed input is rewritten and its signature silently invalidated. Form fields are dropped and their values flattened into page content |
| CHONK-007 | An input that already fits is still rewritten, and the rewrite is larger (standard fonts get embedded). With a ceiling equal to its own size, the tool reports "No tested profile reached the size ceiling" |
| CHONK-008 | `validate_pdf` misses reordered, rotated, resized, and blanked pages, changed digits, a removed text layer, and removed form values |
| CHONK-009 | Mean page similarity dilutes one damaged page by the page count. Renders never initialize forms, so field values are invisible to the comparison |
| CHONK-010 | Search reports failure when both endpoints miss although an interior profile fits. It reports the last endpoint rather than the smallest tested size. On a non-monotonic ladder it misses the best fitting profile even with a full budget |

Other baseline facts the tests record without treating them as defects of
this task:

- The real Ghostscript ladder is not monotonic in size. With
  `-dPassThroughJPEGImages`, QFactor has no effect at source resolution on
  JPEG input. The source-resolution profiles all produce the same size, and
  the 600 dpi profiles interleaved with them are slightly larger
  (`test_real_ladder_sizes_are_not_monotonic`).
- Ghostscript folds `/Rotate` into the MediaBox. The oracle compares the
  displayed page size, so this is not reported as damage.
- Encrypted PDFs are refused even when the user password is empty.
- `pypdfium2` 4.x cannot run the baseline: `compare_visual_similarity` uses
  `PdfDocument` as a context manager, which requires pypdfium2 5.0.
  `requirements.txt` still allows `>=4.30`.
