"""CHONK's privacy-mode MCP server (stdio).

Claude Code starts this as its own process, outside the Bash sandbox, so it
can read the vault while nothing the agent runs can. The tools return numbers
and fixed labels only; see :mod:`chonk.private`.

There is deliberately no render, crop, preview, text-extraction or path tool.
If the tool does not exist, the agent cannot call it.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import warnings
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from .private import PrivateService
from .vault import Vault

INSTRUCTIONS = """\
CHONK compresses PDFs to a byte limit without showing you the document.
You never see the file's name, path, text, pixels or metadata, and no tool
here can return them. Results are numbers and fixed labels.

Workflow:
1. select_document opens a file picker on the user's screen. Ask the user to
   choose the file there. Never ask them to type or paste a path or file name.
   If the picker is unavailable (headless machine), use list_vault; the user
   identifies their file by page count, size and age.
2. compress(doc, target) with the handle and the size the user needs (for
   example "200KB"). CHONK searches for the clearest version that fits, checks
   it on this machine (worst-page SSIM, OCR character comparison) and, unless
   review=false, opens a window where the user compares the worst page and
   clicks Accept or Reject. The file is saved to the user's vault outbox (or
   a save dialog with save="dialog"). You get a label, not the file.
3. If privacy_posture is not "walled", tell the user their private folder is
   not fully locked and suggest running: chonk doctor --privacy

Report results plainly: size achieved against the limit, worst_page_ssim,
OCR characters compared and mismatches, the user's review decision, and
anything listed in "lost". Never ask the user to share the document's
contents with you; the point of this server is that you do not need them.
"""


def configure_logging(vault: Vault) -> None:
    """Log to a file inside the vault only. Nothing goes to stderr or stdout.

    Library messages (pypdf, pdfium) can quote document objects, so they are
    kept where the agent cannot read them when the vault is locked.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    try:
        vault.ensure()
        log_path = vault.state_dir / "chonk.log"
        log_path.touch(mode=0o600, exist_ok=True)
        log_path.chmod(0o600)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    except OSError:
        handler = logging.NullHandler()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)
    warnings.simplefilter("default")


def build_server(service: PrivateService):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    server = MCPServer(name="chonk", instructions=INSTRUCTIONS, log_level="ERROR")
    local_only = dict(open_world_hint=False)

    @server.tool(
        description=(
            "Open a native file picker on the user's screen so they can choose a PDF. "
            "Returns an opaque handle plus page count, byte size and a document-kind label. "
            "Never returns the file name or path."
        ),
        annotations=ToolAnnotations(title="Choose a document", read_only_hint=True, **local_only),
    )
    def select_document() -> dict[str, Any]:
        return service.select_document()

    @server.tool(
        description=(
            "Headless fallback: list PDFs in the user's vault folder as opaque handles with "
            "page count, byte size, kind and age in days. No names or paths."
        ),
        annotations=ToolAnnotations(title="List vault documents", read_only_hint=True, **local_only),
    )
    def list_vault() -> dict[str, Any]:
        return service.list_vault()

    @server.tool(
        description=(
            "Compress a selected document to at most `target` bytes (e.g. '200KB', '2MB', "
            "'1.5MiB'). Returns sizes, worst-page SSIM, OCR character counts, the user's "
            "review decision and where the file was saved. Never returns content."
        ),
        annotations=ToolAnnotations(title="Compress a document", read_only_hint=False,
                                    destructive_hint=False, idempotent_hint=False, **local_only),
    )
    def compress(
        doc: Annotated[str, Field(description="Handle from select_document or list_vault, e.g. doc_1a2b3c4d")],
        target: Annotated[str, Field(description="Maximum size: 200KB, 2MB, 1.5MiB (KB/MB = 1000s, KiB/MiB = 1024s)")],
        allow_grayscale: Annotated[bool, Field(description="Allow converting colour images to grayscale if colour cannot fit")] = False,
        strip_metadata: Annotated[bool, Field(description="Remove title/author/XMP metadata from the output")] = False,
        review: Annotated[bool, Field(description="Open a local window so the user can accept or reject the worst page")] = True,
        ocr: Annotated[bool, Field(description="Compare OCR character counts of original and result, locally")] = True,
        save: Annotated[str, Field(description="'outbox' (vault/outbox) or 'dialog' (user picks a location)")] = "outbox",
    ) -> dict[str, Any]:
        return service.compress(doc, target, allow_grayscale=allow_grayscale,
                                strip_metadata=strip_metadata, review_result=review,
                                ocr_check=ocr, save=save)

    @server.tool(
        description=(
            "Check whether Claude Code's settings lock the vault away from the agent. Returns "
            "walled/partial/open plus fixed codes for anything missing."
        ),
        annotations=ToolAnnotations(title="Privacy status", read_only_hint=True, **local_only),
    )
    def privacy_status() -> dict[str, Any]:
        return service.privacy_status()

    return server


def main(vault_root: Path | None = None) -> int:
    vault = Vault(vault_root)
    configure_logging(vault)
    server = build_server(PrivateService(vault))
    configure_logging(vault)  # the SDK may have installed its own handlers
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
