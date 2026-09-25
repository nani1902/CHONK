# CHONK

**Fit a PDF under a size limit while keeping the clearest result CHONK can find.**

CHONK is a local Python desktop app and command-line tool. Give it a file-size
ceiling such as `2 MB`; it searches compression profiles, renders each tested
candidate, and keeps the best visual match that fits. The size ceiling is a
hard byte limit (`2 MB` means 2,000,000 bytes). It does not upload documents or
run telemetry.

## Desktop app

### Requirements

- Python 3.10+
- Tkinter support for the desktop interface (some Linux distributions package
  it separately, for example as `python3-tk`)
- Ghostscript
- The Python packages in `requirements.txt`

Ghostscript is installed separately and is not bundled with CHONK. Get it from
the [official downloads page](https://www.ghostscript.com/releases/gsdnld.html).

### Run from source

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
brew install ghostscript  # macOS; use your Linux package manager on Linux
python app.py
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Install Ghostscript from its official Windows installer before compressing.
The GUI lets users select a PDF, choose an output path, set the maximum size,
and watch the profile search. The original file is never overwritten.

To build a local desktop bundle, install PyInstaller and run:

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --name CHONK \
  --paths src --collect-all pypdfium2 --collect-all pypdfium2_raw \
  --add-data "THIRD_PARTY_NOTICES.md:." app.py
```

The built app still needs Ghostscript installed separately. Binary
redistribution must include notices for bundled PDFium components; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Command line

```bash
python pdf_compressor.py input.pdf --target-size 2MB
```

By default, the result is `input-compressed.pdf`. Choose an output path and a
different ceiling like this:

```bash
python pdf_compressor.py input.pdf --target-size 4.5MiB --output smaller.pdf
```

CHONK refuses to overwrite an existing output unless `--force` is supplied.
It never overwrites the input. If no tested profile fits the ceiling, it
reports the smallest candidate and leaves the output untouched. If preflight
inspection refuses the input (see below), it explains why and writes nothing.

Useful options:

```text
--min-dpi 72         Lowest image resolution considered (default: 72)
--max-dpi 600        Highest image resolution considered (default: 600)
--max-attempts 16    Maximum Ghostscript runs (default: 16)
--timeout 900        Timeout per run, in seconds (default: 900)
--comparison-dpi 150 Render resolution used to compare pages (default: 150)
--ghostscript PATH   Explicit Ghostscript executable path
--force              Replace an existing output after successful compression
```

Sizes accept `B`, `KB`, `MB`, `GB`, `KiB`, `MiB`, and `GiB`. `MB` uses decimal
units and `MiB` uses binary units.

Requests are bounded: the target is 1 byte to 10 GiB, `--max-attempts` is 2–64,
`--timeout` is 1–86,400 seconds, DPI values are 1–2,400, and `--comparison-dpi`
is 36–600. Values outside these ranges are rejected before any work starts.

## PDFs CHONK will not compress

Before running Ghostscript, CHONK inspects the source PDF. Ghostscript rewrites
the whole file, and CHONK cannot yet check that the rewrite preserves these
features. So it refuses PDFs that contain any of them:

- encryption, including PDFs that open without a password
- digital signatures (a rewrite always invalidates them)
- interactive or XFA form fields
- JavaScript, launch actions, and other active content
- attached files
- comments and other annotations, links, and bookmarks
- accessibility tags
- layers (optional content)

CHONK also refuses a PDF when it cannot rule these features out. That happens
when the file is damaged in a way that hides part of its structure, when it
holds a script object that nothing references, or when the parser and the
renderer disagree about the page count. Refusals exit with status 2, name
the features involved, and leave the original untouched.

This is stricter than earlier versions of CHONK, which compressed PDFs with
links, bookmarks, annotations, forms, or signatures and silently lost or
invalidated some of them. Common PDFs are affected, for example
word-processor exports with links or tags. The complete matrix, the
detection method, and the known limitations are in
[`docs/product/SUPPORTED_FEATURES.md`](docs/product/SUPPORTED_FEATURES.md).
Support for individual features will return as validators that prove their
preservation are added.

## How the search works

1. Try to preserve source image resolution and pass through supported JPEG and
   JPEG 2000 images.
2. If that exceeds the ceiling, search profiles that balance image resolution
   and image quality.
3. Render all pages of each tested candidate with PDFium and compare them to
   the source. Select the highest similarity among the tested candidates that
   fit the ceiling.
4. Validate the output PDF, page count, and final byte size before writing it.

The finite search is a practical quality optimization, not a guarantee of a
global optimum or a perfect measure of human perception. Long PDFs can take
time because each candidate is rendered and compared page by page.

Ghostscript rewrites the PDF. Metadata, page labels, and other information
that CHONK does not inspect may change. See the
[Ghostscript PDF output documentation](https://ghostscript.readthedocs.io/en/latest/VectorDevices.html#the-family-of-pdf-and-postscript-output-devices).

## How CHONK can stand out

PDF24 Creator offers offline compression on Windows with adjustable DPI and
image quality; Stirling PDF offers compression as one of many PDF tools, with
desktop and self-hosted options. Acrobat also offers compression presets and
batch workflows. CHONK is narrower by design:

- Make the requested maximum file size the primary input, rather than making
  people guess at a compression preset.
- Show the measured size and visual similarity for every attempted profile so
  the quality/size trade-off is visible.
- Add side-by-side page previews, per-page quality warnings, and optional
  settings to preserve OCR text, metadata, forms, and annotations.
- Offer a batch queue that finds a different best-fit profile for each file.

The first two bullets are implemented by the current CLI search and the GUI's
compression log; previews, per-page controls, and batch processing are future
work. Competitor feature pages: [PDF24 Creator](https://tools.pdf24.org/en/creator),
[Stirling PDF](https://github.com/Stirling-Tools/Stirling-PDF), and
[Acrobat compression](https://helpx.adobe.com/acrobat/web/share-review-and-export/export-and-print/compress-pdfs.html).

## Code layout and tests

`app.py` and `pdf_compressor.py` are compatibility launchers. The
implementation is the `chonk` package in `src/chonk/`: `engine.py`
orchestrates inspection, the profile search (`search.py`), the Ghostscript
backend (`backends/ghostscript.py`), and output checks (`validation/`). The
engine takes a typed `CompressionRequest`, reports progress through typed
events, and returns a `CompressionResult`; it does not print or import a user
interface. `adapters/` holds the command line and shared text formatting.

```python
from pathlib import Path
from chonk import CompressionRequest, compress_pdf, parse_size

result = compress_pdf(
    CompressionRequest(
        source=Path("input.pdf"),
        output=Path("smaller.pdf"),
        target_bytes=parse_size("2MB"),
    )
)
print(result.status.value, result.attempt_count)
```

This Python API is internal and may change. The machine-readable contract for
future JSON CLI and MCP callers is defined separately as schema version `1.0`:
typed requests and results, check states, reason codes, completion basis, and
versioned preservation policies (`chonk.models`, `chonk.policies`,
`chonk.contract`, and the JSON Schemas in [`schemas/1.0`](schemas/1.0/README.md)).
The engine and CLI do not emit contract results yet; that wiring comes with
inspection, validation, and the JSON CLI.

To run the tests (Ghostscript tests are skipped when `gs` is not
installed, desktop tests when Tkinter is unavailable):

```bash
python -m pip install -e ".[test]"
python -m pytest
```

## Product development plan

The proposed next stage is private PDF preparation for people and agents, with
preflight inspection, preservation checks, local review, batch processing, and
structured CLI/MCP results. These are planned features, not current capabilities.

See the [development plan](docs/product/README.md) for the detailed product
requirements, current-to-target architecture, implementation tasks, and release
validation criteria.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The suite generates a synthetic PDF corpus at run time and needs Ghostscript
only for its end-to-end tests. See [`tests/README.md`](tests/README.md) for the
corpus, the evaluation checks, and the baseline defects the tests record.

## License

CHONK's source code is under the PolyForm Noncommercial License 1.0.0. That
permits noncommercial use, modification, and redistribution under its terms,
but it is **source-available rather than open source** under the Open Source
Definition because it limits commercial use. See [`LICENSE`](LICENSE) and the
[Open Source Definition](https://opensource.org/osd).

Ghostscript and the Python packages have separate licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). CHONK does not bundle
Ghostscript.
