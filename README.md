# CHONK

**Fit a PDF under a size limit, locally, keeping the clearest version that fits.**

CHONK has two uses:

1. **Privacy mode.** It's an MCP server, so an AI agent such as Claude Code
   can compress your passport scan to 200 KB **without seeing it**. The agent
   gets numbers and fixed labels. It never sees the file name, path, text,
   pixels or metadata. Claude Code's own settings stop the agent opening the
   file some other way.
2. **A plain CLI and desktop app** for everything else.

It runs on your machine. No uploads, no telemetry, no network code, and no
Ghostscript.

---

## Privacy mode

> Your AI assistant can shrink your passport scan to 200 KB, confirm every
> confidently-read character survived, and never see a single pixel. This page
> gives the settings that enforce that, and the source is open for you to check.

### Why a tool alone isn't enough

A tool controls only what it returns. It can't stop the agent from opening
the file some other way. In Claude Code, with nothing configured, an agent can
open a PDF with its Read tool, which renders PDFs into the model's context. It
can run `pdftotext`, or write a Python script that OCRs the file. Claude Code's
permission docs say Read deny rules cover the built-in file tools and the
shell commands Claude Code recognises. They don't cover "arbitrary subprocesses
that read or write files indirectly, like a Python or Node script that opens
files itself". For that the docs point to the OS-level Bash sandbox.

So privacy mode needs two parts, and you need both:

| Part | Who enforces it | What it stops |
|---|---|---|
| **The lock** (Claude Code settings) | Claude Code, and the OS sandbox (Seatbelt on macOS, bubblewrap on Linux/WSL2) | The agent reading `~/Private` with its tools, shell commands, or scripts it writes |
| **The tool** (this MCP server) | CHONK's code, which you can read | CHONK handing content back to the agent |

The Claude Code sandbox applies only to Bash, PowerShell and Monitor commands.
An MCP server is a separate process outside it. CHONK can read `~/Private`;
nothing the agent runs can. For the same reason privacy mode is an MCP server,
not a skill: a skill runs CHONK through Bash, where it's sandboxed and can't
read the vault either.

### Setup

```bash
# 1. Install (Python 3.11+)
python -m pip install -r requirements.txt
python -m pip install --no-deps -e .
# Optional offline OCR check. Pick one:
#   brew install tesseract        (or apt install tesseract-ocr, or the UB Mannheim Windows build)
#   python -m pip install "rapidocr-onnxruntime>=1.4,<2"   (Python < 3.13; its ONNX models ship in the wheel)

# 2. Make the vault and put sensitive PDFs in it
mkdir -p ~/Private

# 3. Lock it (merges into ~/.claude/settings.json and backs that file up first)
chonk doctor --privacy --apply

# 4. Register the server with Claude Code
claude mcp add --scope user chonk -- "$(python -c 'import sys; print(sys.executable)')" -m chonk mcp

# 5. Restart Claude Code, then check
chonk doctor --privacy          # should print WALLED
```

The lock that `--apply` adds (also in [`examples/claude-settings.json`](examples/claude-settings.json)):

```json
{
  "permissions": {
    "deny": ["Read(~/Private/**)", "Edit(~/Private/**)"],
    "disableBypassPermissionsMode": "disable"
  },
  "sandbox": {
    "enabled": true,
    "allowUnsandboxedCommands": false,
    "filesystem": { "denyRead": ["~/Private"] }
  }
}
```

- The **deny rules** block the Read tool (including PDF rendering), `@file`
  mentions, and the shell file commands Claude Code recognises.
- **`sandbox.filesystem.denyRead`** is enforced by the OS for every sandboxed
  shell command and its child processes, so the "write a Python OCR script"
  route fails too.
- **`allowUnsandboxedCommands: false`** closes the `dangerouslyDisableSandbox`
  retry, which otherwise lets the agent ask to run a command outside the
  sandbox.
- **`disableBypassPermissionsMode`** stops a session from starting in bypass
  mode. It's hardening; drop it if you rely on bypass mode elsewhere.
- The sandbox also blocks shell commands from writing to `.mcp.json` and
  `.claude/` settings, so the agent can't install its own tool to get around
  the lock.

Using a different folder: set `CHONK_VAULT=/path/to/folder` for both the
server and `chonk doctor`, or pass `--vault`.

**Extra protection on Linux:** `chonk mcp --no-network` re-runs the server
in a fresh network namespace, using bubblewrap or `unshare`, so the kernel
blocks CHONK's network access. X11 and Wayland file pickers still open. It
is not implemented on macOS or Windows yet; there the flag refuses to start
rather than giving you a false promise.

### Tools

There is deliberately no render, crop, preview, extract-text or path tool.
If the tool doesn't exist, the agent can't call it.

| Tool | What the user sees | What the agent gets |
|---|---|---|
| `select_document()` | A native file picker | `{"status": "selected", "doc": "doc_7f3a09c1", "pages": 2, "bytes": 4812201, "kind": "scanned_images", "in_vault": true}` |
| `list_vault()` | Nothing (headless fallback) | Handles with `pages`, `bytes`, `kind`, `modified_days_ago`. No names |
| `compress(doc, target, …)` | A window showing the worst page, before and after, with Accept and Reject | See below |
| `privacy_status()` | Nothing | `walled` / `partial` / `open` plus fixed codes for what's missing |

Example `compress` result (illustrative values):

```json
{"status": "fit", "bytes": 291078, "target_bytes": 300000, "source_bytes": 1641467,
 "pages": 2, "worst_page": 0, "worst_page_ssim": 0.9741, "mean_ssim": 0.9743,
 "max_dpi": 150, "jpeg_quality": 30, "grayscale": false, "lost": [],
 "ocr_status": "compared", "ocr_engine": "tesseract", "ocr_pages_compared": 2,
 "ocr_chars_compared": 412, "ocr_mismatches": 0,
 "user_review": "approved", "saved_to": "vault_outbox", "privacy_posture": "walled"}
```

`compress` options: `allow_grayscale` (default false), `strip_metadata`
(false), `review` (true), `ocr` (true), `save` (`"outbox"` saves to
`~/Private/outbox/`; `"dialog"` opens a save dialog).

**How the output stays content-free:**

- **Choosing the file:** You pick it in a native dialog that a child process
  opens. The path goes to the CHONK process only. Handles are random, never
  derived from the name. A name like `passport_ravi_kumar.pdf` is itself a
  leak.
- **Output:** Every value is a number, a boolean, `null`, a handle, or a label
  from a fixed list. `chonk/private.py` checks every result against that list
  and replaces anything else with `{"status": "error:internal"}`.
- **Errors:** Every error becomes a fixed code (`error:encrypted`,
  `error:unreadable`, …). Library messages can quote document objects, so
  they're logged to `~/Private/.chonk/chonk.log` (mode 0600) and never
  returned.
- **Metadata:** Title, Author and XMP are never read into any result.
  `strip_metadata=true` also removes them from the output file.
- **Logs:** The server writes nothing to stdout or stderr except the MCP
  protocol. The CLI never prints the input path.
- **Review window and OCR:** Both run on your machine. Page images reach the
  review window over a pipe, never a temp file the agent could read.
- **Network:** CHONK contains no network code. A test fails if any CHONK module
  imports a networking library, and CI runs the whole suite inside a Linux
  network namespace.

### Checking quality without the agent seeing anything

- **SSIM, worst page.** SSIM is structural similarity (Wang et al., 2004;
  Gaussian window, σ = 1.5, validated against scikit-image to 6 decimal
  places). CHONK measures it on your machine and reports the worst page rather
  than the average, because an average can hide one ruined page. 1.0 means
  identical.
- **OCR, counts only.** CHONK OCRs the original and the result, locally,
  starting with the worst page (up to 10 pages). It counts only the
  characters in words OCR read *confidently* (≥ 90/100) on the original.
  `ocr_mismatches` is how many of those characters are in words that don't
  reappear unchanged. In CHONK's own test, on clean synthetic printed text
  (about 16 pt, scanned at 200 dpi), both Tesseract and RapidOCR reported 0
  mismatches down to 72 dpi / JPEG quality 30, and both caught the breakage
  at 50 dpi. **Limits:** on blurry or low-contrast scans, OCR itself
  is noisy. Near-lossless output can then show a few dozen mismatches caused
  by OCR, not compression. OCR also says nothing about photos, holograms or
  signatures.
- **You check it, not the agent.** The review window shows the worst page
  side by side, with a synchronised "actual size" view. Rejecting or closing
  it saves nothing. With no display (for example over SSH) the result says
  `user_review: "unavailable"` and the file is still saved, so tell the user
  to look at it.

### What still leaks, and the limits

| Reaches the model provider | Doesn't |
|---|---|
| That a 2-page, 4.8 MB PDF became 198 KB | Names, numbers, photo, MRZ, filename, path, metadata |
| Timing, SSIM, OCR counts, resolution and quality settings | Pixels, text, OCR output |
| **Anything you type in chat** ("shrink my passport, number X…") | |

- **Native Windows has no sandbox.** The Claude Code sandbox supports macOS,
  Linux and WSL2 only. On native Windows only the deny rules apply, and a
  Python script gets around them. `chonk doctor --privacy` reports
  `partial` there, never `walled`. Use WSL2 if this matters to you.
- **Settings are only as good as their configuration.** A disabled sandbox,
  an `excludedCommands` entry, an `allowRead` that re-opens the vault, or
  approving a prompt without reading it undoes the lock. Every CHONK result
  carries `privacy_posture`, so the agent can warn you. That's a static
  reading of the settings files on disk. It's a warning, not enforcement, and
  it can't see `--settings` flags or server-managed settings.
- **You still have to trust CHONK.** It's the one process allowed to read the
  file. That's why it's Apache-2.0 open source, has few dependencies
  (versions pinned in `requirements.txt`), and offers `--no-network`. The MCP
  SDK brings HTTP libraries that CHONK never uses. On Linux, `--no-network`
  makes that moot.
- **Numbers are a narrow side channel.** A compromised agent calling
  `compress` many times with different targets learns a size/quality curve
  and nothing else.
- **Prompt injection is inert.** A document that never enters the model's
  context can't inject instructions into it. A PDF with hidden "ignore
  previous instructions" text does nothing against a tool that returns only
  numbers.

---

## Command line

```bash
chonk compress input.pdf --target-size 2MB
chonk compress scan.pdf -t 200KB -o upload.pdf --ocr-check
chonk compress scan.pdf -t 200KB --json          # machine-readable, numbers only
chonk inspect scan.pdf                            # pages, bytes, kind, signed
chonk doctor                                      # OCR engine, display, network isolation
chonk doctor --privacy                            # the Claude Code lock
```

Sizes accept `B`, `KB`, `MB`, `GB` (powers of 1000) and `KiB`, `MiB`, `GiB`
(powers of 1024). The limit is hard: `200KB` means at most 200,000 bytes.

CHONK never overwrites the input and won't replace an existing output
without `--force`. Exit codes: `0` saved, `3` nothing fit (nothing written),
`2` error.

| Option | Default | |
|---|---|---|
| `--min-dpi` | 72 | Lowest image resolution considered |
| `--allow-grayscale` | off | Convert colour images to grayscale if colour can't fit |
| `--strip-metadata` | off | Remove title, author and XMP metadata |
| `--comparison-dpi` | 150 | Render resolution for SSIM |
| `--max-attempts` | 48 | Maximum candidates built |
| `--ocr-check` | off | Compare OCR (needs Tesseract or RapidOCR) |

A Claude Code skill for non-sensitive PDFs is in
[`skills/chonk/SKILL.md`](skills/chonk/SKILL.md). It sends anything personal
to the MCP tools instead.

## Desktop app

```bash
python app.py
```

Needs Tkinter (`python3-tk` on some Linux distributions). To build a bundle:

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --name CHONK \
  --collect-all pypdfium2 --collect-all pypdfium2_raw \
  --add-data "THIRD_PARTY_NOTICES.md:." app.py
```

## How the engine works

Everything happens in memory with pypdf, Pillow and pdfium. No temp files.

1. **Already small enough?** It's returned unchanged.
2. **Lossless pass:** compress content streams and merge identical objects.
3. **Images:** For each resolution cap, from "keep source resolution" down to
   `--min-dpi`, binary-search the highest JPEG quality that fits. Measure the
   worst-page SSIM of each fit and keep the best. Grayscale is tried only if
   allowed, and only if colour can't fit.
4. **Validate** the size, page count and parseability before saving.

Only the images change. Text, fonts, vectors, annotations, form fields and
structure are carried over.

Limits, stated plainly:

- **Images left untouched:** CMYK, Lab, DeviceN and Separation images;
  1-bit scans; stencil and colour-key masks; custom `/Decode` arrays; small
  images. Re-encoding them to JPEG risks wrong colours or broken masks. A
  CMYK-heavy PDF may therefore come back `infeasible`.
- **Resolution** is estimated as image pixels divided by page size. That's a
  lower bound when an image fits within its page, so a 150 dpi cap never
  leaves such an image below 150 dpi.
- **Digital signatures** are invalidated by any rewrite. `lost` then includes
  `digital_signature`. Keep the original if the signature matters.
- The search is finite. It's a practical optimum, not a proven global one.

## Development

```bash
python -m pip install -r requirements.txt pytest && python -m pip install --no-deps -e .
python -m pytest -q
```

Every test runs with Python sockets blocked (`tests/conftest.py`). CI also
runs the suite inside a Linux network namespace, after first checking that a
connection from inside it fails. The suite plants a canary string in a test
document's filename, metadata and page text, then asserts it never appears
in any tool result, error, stdout or stderr, or in the full MCP transcript.
All test PDFs are synthetic.

## License

Apache License 2.0; see [`LICENSE`](LICENSE). A privacy claim is only
believable if anyone can read the code, so the license is part of the
product. Dependencies have their own licenses; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
