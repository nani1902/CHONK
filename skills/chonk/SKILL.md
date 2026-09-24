---
name: chonk
description: Compress a PDF to fit an upload size limit (for example "under 200 KB") while keeping it as legible as possible. Use for non-sensitive PDFs with the chonk CLI. For passports, IDs, bank statements or anything personal, use the chonk MCP tools instead so the document is never opened.
---

# CHONK: fit a PDF under a size limit

## First decide: is the document sensitive?

If the user mentions a passport, ID card, visa, driving licence, bank or tax
document, medical record, payslip, or anything personal: **do not open, read,
render or run commands on the file.** Use the `chonk` MCP server's tools:

1. `select_document` opens a file picker on the user's screen. Ask them to pick
   the file there, not to type or paste its name.
2. `compress(doc, target)` returns numbers and fixed labels only.
3. If `privacy_posture` is not `walled`, tell the user to run
   `chonk doctor --privacy` and follow its instructions.

If the MCP server is not installed, tell the user so and point them to the
"Privacy mode" section of the CHONK README. Do not fall back to the CLI for a
sensitive file.

## Non-sensitive documents: the CLI

```bash
chonk compress INPUT.pdf --target-size 2MB --json
```

- Sizes: `KB`/`MB` are powers of 1000; `KiB`/`MiB` are powers of 1024. Portals
  usually mean 1000 unless they say otherwise, so `200KB` = 200,000 bytes.
- Output goes to `INPUT-compressed.pdf` unless `--output` is given. CHONK
  never overwrites the input, and won't replace an existing output without `--force`.
- Exit codes: `0` saved; `3` no setting fit (nothing written; the JSON has
  `smallest_bytes`); `2` error.
- If it does not fit, offer `--allow-grayscale` (colour becomes gray), a
  lower `--min-dpi` (less detail), or a larger limit. Ask before choosing.
- Add `--ocr-check` to confirm text survived (needs Tesseract or
  `rapidocr-onnxruntime`).

Report: final size against the limit, `worst_page_ssim` (1.0 = identical;
below about 0.90, suggest the user look at the result), and anything in `lost`.
