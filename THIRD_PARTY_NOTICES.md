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
- **Pillow** — MIT-CMU License (the historical PIL license text, declared as
  `MIT-CMU` in Pillow's package metadata). Pillow wheels bundle further
  libraries whose licenses are appended to the wheel's `LICENSE` file.
  [License](https://github.com/python-pillow/Pillow/blob/main/LICENSE)

When distributing a compiled CHONK bundle, include all required licenses and
notices for the exact dependency builds included in that bundle. Both Pillow
and pypdfium2 bundle FreeType; under the FreeType License a bundle's
documentation must credit The FreeType Project. A PyInstaller bundle also
embeds the Python runtime, Tcl/Tk, and Apache-2.0-licensed PyInstaller
run-time hooks. See [decision
0001](docs/decisions/0001-licensing-and-distribution.md#23-dependencies). This
file does not replace those third-party licenses.
