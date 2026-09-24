# Third-party software notices

CHONK invokes Ghostscript as a separate executable and does not bundle it.
Ghostscript is distributed under the GNU AGPL or a commercial license. Install
it from the [official Ghostscript downloads page](https://www.ghostscript.com/releases/gsdnld.html)
and review its license and any obligations that apply to your distribution or
use.

The Python application depends on:

- **pypdf** — BSD 3-Clause License. [License](https://github.com/py-pdf/pypdf/blob/main/LICENSE)
- **pypdfium2** — Apache-2.0 or BSD-3-Clause for the Python bindings; PDFium
  and its bundled dependencies have additional notices. See the
  [pypdfium2 licensing information](https://github.com/pypdfium2-team/pypdfium2#licensing)
  and [build license directory](https://github.com/pypdfium2-team/pypdfium2/tree/main/BUILD_LICENSES).
- **Pillow** — HPND License. [License](https://github.com/python-pillow/Pillow/blob/main/LICENSE)

When distributing a compiled CHONK bundle, include all required licenses and
notices for the exact dependency builds included in that bundle. This file
does not replace those third-party licenses.
