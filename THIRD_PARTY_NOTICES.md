# Third-party software notices

CHONK is licensed under the Apache License 2.0. It depends on the following
packages, each under its own license. It does not bundle or invoke
Ghostscript.

Core:

- **pypdf**: BSD 3-Clause License. [License](https://github.com/py-pdf/pypdf/blob/main/LICENSE)
- **pypdfium2**: Apache-2.0 or BSD-3-Clause for the Python bindings. PDFium
  and its bundled dependencies have additional notices. See the
  [pypdfium2 licensing information](https://github.com/pypdfium2-team/pypdfium2#licensing).
- **Pillow**: MIT-CMU (HPND-style) License. [License](https://github.com/python-pillow/Pillow/blob/main/LICENSE)
- **NumPy**: BSD 3-Clause License, with bundled components under 0BSD, MIT, Zlib and CC0-1.0. [License](https://github.com/numpy/numpy/blob/main/LICENSE.txt)

Privacy-mode MCP server:

- **mcp** (Model Context Protocol Python SDK): MIT License. Its dependencies have their own licenses. See the
  [SDK repository](https://github.com/modelcontextprotocol/python-sdk) and each
  package pinned in `requirements.txt`.

Optional OCR (installed separately, not bundled):

- **Tesseract OCR**: Apache-2.0. Invoked as a separate executable.
- **rapidocr-onnxruntime**: Apache-2.0. It pulls in ONNX Runtime (MIT),
  OpenCV (Apache-2.0) and other packages with their own licenses.

When distributing a compiled CHONK bundle, include all required licenses and
notices for the exact dependency builds in that bundle. This file does not
replace those licenses.
