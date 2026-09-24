"""Privacy-mode tool logic, independent of the MCP framework.

The one rule: every value a tool returns is a number, a boolean, ``None``, an
opaque handle, or a string from CHONK's fixed vocabulary below. Nothing
derived from a document's text, pixels, metadata, filename or path is ever
returned. :func:`sanitize` enforces this on every result. If a value breaks
the rule, the whole result is replaced by ``{"status": "error:internal"}``
and the event is logged inside the vault.
"""

from __future__ import annotations

import logging
import math
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import engine, ocr, picker, posture, quality, review
from .errors import ChonkError
from .sizes import parse_size
from .vault import Handles, UnknownHandleError, Vault, write_private

log = logging.getLogger("chonk.private")

REVIEW_DPI = 150
OCR_MAX_PAGES = 10
LIST_LIMIT = 25

# ---------------------------------------------------------------------------
# The fixed vocabulary

STATUS = frozenset({
    "selected", "listed", "cancelled", "fit", "already_fits", "infeasible", "ok",
    "error:encrypted", "error:unreadable", "error:not_pdf", "error:empty", "error:too_large",
    "error:unknown_doc", "error:bad_target", "error:bad_argument", "error:file_missing",
    "error:picker_unavailable", "error:io", "error:internal",
})
VOCABULARY: dict[str, frozenset[str]] = {
    "status": STATUS,
    "kind": frozenset({"scanned_images", "scanned_with_text_layer", "mixed", "text_or_vector"}),
    "user_review": frozenset({"approved", "rejected", "dismissed", "unavailable", "skipped", "not_needed"}),
    "saved_to": frozenset({"vault_outbox", "user_location", "not_saved"}),
    "privacy_posture": frozenset({"walled", "partial", "open"}),
    "ocr_status": frozenset({"compared", "unavailable", "skipped", "failed", "not_needed"}),
    "ocr_engine": frozenset({"tesseract", "rapidocr"}),
}
LIST_VOCABULARY: dict[str, frozenset[str]] = {
    "lost": frozenset({"digital_signature", "color", "metadata"}),
    "missing": frozenset(posture.MISSING_CODES),
    "hardening": frozenset(posture.HARDENING),
}
NUMBER_KEYS = frozenset({
    "pages", "bytes", "source_bytes", "target_bytes", "smallest_bytes", "attempts",
    "worst_page", "worst_page_ssim", "mean_ssim", "max_dpi", "jpeg_quality",
    "images_total", "images_reencoded", "ocr_pages_compared", "ocr_chars_compared",
    "ocr_mismatches", "modified_days_ago", "count",
})
BOOL_KEYS = frozenset({"in_vault", "grayscale", "lossless", "sandbox_supported"})
HANDLE_RE = re.compile(r"doc_[0-9a-f]{8}")


class LeakError(Exception):
    """A result held a value outside the fixed vocabulary."""


def _clean(key: str, value: Any) -> Any:
    if key == "doc":
        if isinstance(value, str) and HANDLE_RE.fullmatch(value):
            return value
        raise LeakError(key)
    if key in VOCABULARY:
        if isinstance(value, str) and value in VOCABULARY[key]:
            return value
        raise LeakError(key)
    if key in LIST_VOCABULARY:
        if isinstance(value, (list, tuple)) and all(
                isinstance(item, str) and item in LIST_VOCABULARY[key] for item in value):
            return list(value)
        raise LeakError(key)
    if key in BOOL_KEYS:
        if isinstance(value, bool):
            return value
        raise LeakError(key)
    if key in NUMBER_KEYS:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LeakError(key)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise LeakError(key)
            return round(value, 4)
        return value
    if key == "documents":
        if isinstance(value, list):
            return [_clean_dict(item) for item in value]
        raise LeakError(key)
    raise LeakError(key)


def _clean_dict(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise LeakError("result")
    return {key: _clean(key, value) for key, value in result.items()}


def sanitize(result: dict[str, Any]) -> dict[str, Any]:
    try:
        return _clean_dict(result)
    except LeakError as exc:
        log.error("blocked a result with a disallowed value under key %r", str(exc))
        return {"status": "error:internal"}


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, ChonkError):
        return exc.code
    if isinstance(exc, UnknownHandleError):
        return "error:unknown_doc"
    if isinstance(exc, FileNotFoundError):
        return "error:file_missing"
    if isinstance(exc, OSError):
        return "error:io"
    return "error:internal"


# ---------------------------------------------------------------------------
# The service


class PrivateService:
    """The four privacy-mode tools. Every public method returns a sanitized dict."""

    def __init__(
        self,
        vault: Vault | None = None,
        *,
        pick_open: Callable[[Path], picker.PickResult] | None = None,
        pick_save: Callable[[Path, str], picker.PickResult] | None = None,
        reviewer: Callable[..., review.Decision] | None = None,
        ocr_engine_factory: Callable[[], ocr.OcrEngine | None] | None = None,
        posture_check: Callable[[Path], posture.PostureReport] | None = None,
    ):
        self.vault = vault or Vault()
        self.handles = Handles()
        self._pick_open = pick_open or (lambda initial: picker.pick_open(initial))
        self._pick_save = pick_save or (lambda initial, name: picker.pick_save(initial, name))
        self._reviewer = reviewer or review.request_review
        self._ocr_factory = ocr_engine_factory or ocr.find_engine
        self._posture_check = posture_check or (lambda vault: posture.check(vault))
        self._ocr_engine: ocr.OcrEngine | None = None
        self._ocr_loaded = False
        # pdfium is not thread-safe, and one dialog at a time is enough.
        self._lock = threading.Lock()

    # -- helpers -----------------------------------------------------------

    def _posture(self) -> str:
        try:
            return self._posture_check(self.vault.root).posture
        except Exception:
            log.exception("posture check failed")
            return "open"

    def _guarded(self, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            try:
                result = work()
            except Exception as exc:  # map to a code; details stay in the vault log
                code = _error_code(exc)
                if code == "error:internal":
                    log.exception("tool failed")
                else:
                    log.info("tool returned %s (%s)", code, type(exc).__name__)
                result = {"status": code}
            result.setdefault("privacy_posture", self._posture())
            return sanitize(result)

    def _describe(self, path: Path) -> dict[str, Any]:
        data = engine.read_input(path)
        info = engine.inspect(data)
        return {"pages": info.pages, "bytes": info.bytes, "kind": info.kind}

    def _ocr(self) -> ocr.OcrEngine | None:
        if not self._ocr_loaded:
            self._ocr_engine = self._ocr_factory()
            self._ocr_loaded = True
        return self._ocr_engine

    # -- tools -------------------------------------------------------------

    def select_document(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            initial = self.vault.root if self.vault.root.is_dir() else Path.home()
            state, path = self._pick_open(initial)
            if state == "cancelled":
                return {"status": "cancelled"}
            if state != "selected" or path is None:
                return {"status": "error:picker_unavailable"}
            described = self._describe(path)
            return {"status": "selected", "doc": self.handles.handle_for(path),
                    "in_vault": self.vault.contains(path), **described}
        return self._guarded(work)

    def list_vault(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            now = time.time()
            documents = []
            for path in self.vault.documents()[:LIST_LIMIT]:
                entry: dict[str, Any] = {"doc": self.handles.handle_for(path)}
                try:
                    entry.update(self._describe(path))
                    entry["status"] = "ok"
                except Exception as exc:
                    entry["status"] = _error_code(exc)
                try:
                    entry["modified_days_ago"] = int((now - path.stat().st_mtime) // 86400)
                except OSError:
                    entry["modified_days_ago"] = None
                documents.append(entry)
            return {"status": "listed", "count": len(documents), "documents": documents}
        return self._guarded(work)

    def privacy_status(self) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            report = self._posture_check(self.vault.root)
            return {"status": "ok", "privacy_posture": report.posture, "missing": report.missing,
                    "hardening": report.hardening, "sandbox_supported": sys.platform != "win32"}
        return self._guarded(work)

    def compress(self, doc: str, target: str | int, allow_grayscale: bool = False,
                 strip_metadata: bool = False, review_result: bool = True,
                 ocr_check: bool = True, save: str = "outbox") -> dict[str, Any]:
        def work() -> dict[str, Any]:
            if save not in ("outbox", "dialog"):
                return {"status": "error:bad_argument"}
            try:
                target_bytes = parse_size(target)
            except (ValueError, TypeError):
                return {"status": "error:bad_target"}
            source_path = self.handles.path_for(doc)
            source = engine.read_input(source_path)
            result = engine.compress_bytes(source, engine.Options(
                target_bytes=target_bytes,
                allow_grayscale=bool(allow_grayscale),
                strip_metadata=bool(strip_metadata),
            ))
            out: dict[str, Any] = {
                "status": result.status,
                "doc": doc,
                "source_bytes": result.source_bytes,
                "target_bytes": target_bytes,
                "pages": result.pages,
                "attempts": result.attempts,
            }
            if result.status == "infeasible" or result.output is None:
                out.update({"smallest_bytes": result.smallest_bytes, "saved_to": "not_saved",
                            "user_review": "not_needed", "ocr_status": "not_needed"})
                return out

            profile = result.profile or engine.LOSSLESS
            out.update({
                "bytes": result.output_bytes,
                "worst_page": result.worst_page_index,
                "worst_page_ssim": result.worst_page_ssim,
                "mean_ssim": result.mean_ssim,
                "lossless": profile.lossless,
                "max_dpi": profile.max_dpi,
                "jpeg_quality": profile.quality,
                "grayscale": profile.grayscale,
                "images_total": result.images_total,
                "images_reencoded": result.images_reencoded,
                "lost": list(result.lost),
            })
            unchanged = result.status == "already_fits"
            out.update(self._ocr_fields(source, result, unchanged or not ocr_check))
            decision = self._review(source, result, unchanged, review_result)
            out["user_review"] = decision
            if decision in ("rejected", "dismissed"):
                out["saved_to"] = "not_saved"
                return out
            out["saved_to"] = self._save(source_path, result.output, save)
            return out
        return self._guarded(work)

    # -- compress helpers ----------------------------------------------------

    def _ocr_fields(self, source: bytes, result: engine.Result, skip: bool) -> dict[str, Any]:
        if skip:
            status = "not_needed" if result.status == "already_fits" else "skipped"
            return {"ocr_status": status, "ocr_chars_compared": None, "ocr_mismatches": None}
        engine_ = self._ocr()
        if engine_ is None:
            return {"ocr_status": "unavailable", "ocr_chars_compared": None, "ocr_mismatches": None}
        worst = result.worst_page_index or 0
        pages = [worst] + [i for i in range(result.pages) if i != worst]
        try:
            comparison = ocr.compare(source, result.output or b"", engine_, pages[:OCR_MAX_PAGES])
        except Exception as exc:
            log.warning("OCR comparison failed: %s", type(exc).__name__)
            return {"ocr_status": "failed", "ocr_chars_compared": None, "ocr_mismatches": None}
        return {"ocr_status": "compared", "ocr_engine": engine_.name,
                "ocr_pages_compared": comparison.pages_compared,
                "ocr_chars_compared": comparison.chars_compared,
                "ocr_mismatches": comparison.mismatches}

    def _review(self, source: bytes, result: engine.Result, unchanged: bool, wanted: bool) -> str:
        if unchanged:
            return "not_needed"
        if not wanted:
            return "skipped"
        index = result.worst_page_index or 0
        try:
            original_png = quality.render_page_png(source, index, REVIEW_DPI)
            compressed_png = quality.render_page_png(result.output or b"", index, REVIEW_DPI)
        except Exception as exc:
            log.warning("could not render the review page: %s", type(exc).__name__)
            return "unavailable"
        return self._reviewer(
            original_png, compressed_png, page=index, pages=result.pages,
            ssim=result.worst_page_ssim or 0.0, output_bytes=result.output_bytes or 0,
            target_bytes=result.target_bytes,
        )

    def _save(self, source_path: Path, data: bytes | None, mode: str) -> str:
        if data is None:
            return "not_saved"
        if mode == "dialog":
            state, chosen = self._pick_save(self.vault.outbox if self.vault.outbox.is_dir()
                                            else self.vault.root, f"{source_path.stem}-chonk.pdf")
            if state == "cancelled":
                return "not_saved"
            if state == "selected" and chosen is not None:
                if chosen.resolve() == source_path.resolve():
                    log.info("refused to overwrite the input with the output")
                    return "not_saved"
                write_private(chosen, data, overwrite=True)  # the dialog confirmed any overwrite
                return "user_location"
            # No dialog available: fall back to the outbox.
        self.vault.ensure()
        write_private(self.vault.outbox_path(source_path), data)
        return "vault_outbox"
