"""Privacy mode: nothing derived from the document may appear in any result.

The canary string ``pdfs.SECRET`` is planted in the file name, the metadata and
the page text. Every tool result, every log line that reaches stdout/stderr,
and every error is checked for it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pdfs
import pytest

from chonk import engine, posture
from chonk.private import PrivateService, sanitize
from chonk.vault import Vault


def no_leak(result: dict, *extra: str) -> None:
    blob = json.dumps(result)
    for needle in (pdfs.SECRET, "passport", *extra):
        assert needle not in blob, f"{needle!r} leaked into {blob}"


class Reviewer:
    def __init__(self, decision: str = "approved"):
        self.decision = decision
        self.calls: list[dict] = []

    def __call__(self, original_png: bytes, compressed_png: bytes, **info):
        assert original_png.startswith(b"\x89PNG") and compressed_png.startswith(b"\x89PNG")
        self.calls.append(info)
        return self.decision


class FakeOcr:
    name = "tesseract"

    def words(self, image):
        return [("PASSPORT", 99.0), ("NUMBER", 97.0), (pdfs.SECRET, 95.0)]


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    vault = Vault(tmp_path / "Private")
    vault.ensure()
    return vault


@pytest.fixture
def document(vault: Vault, scanned: bytes) -> Path:
    path = vault.root / f"passport_{pdfs.SECRET}.pdf"
    path.write_bytes(scanned)
    return path


def walled(_vault):
    return posture.PostureReport("walled")


def make(vault: Vault, document: Path | None = None, *, pick="selected", reviewer=None,
         ocr_engine=None, save_to: Path | None = None, save_state="selected") -> PrivateService:
    return PrivateService(
        vault,
        pick_open=lambda initial: (pick, document if pick == "selected" else None),
        pick_save=lambda initial, name: (save_state, save_to),
        reviewer=reviewer or Reviewer(),
        ocr_engine_factory=lambda: ocr_engine,
        posture_check=walled,
    )


# -- sanitizer --------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"status": "fit", "note": "free text"},
    {"status": "Could not read /Users/x/passport.pdf"},
    {"doc": "passport.pdf"},
    {"kind": pdfs.SECRET},
    {"lost": ["metadata", pdfs.SECRET]},
    {"bytes": "123"},
    {"bytes": True},
    {"worst_page_ssim": float("nan")},
    {"in_vault": 1},
    {"documents": [{"doc": "doc_12345678", "title": pdfs.SECRET}]},
])
def test_sanitize_blocks_anything_outside_the_vocabulary(bad):
    assert sanitize(bad) == {"status": "error:internal"}


def test_sanitize_passes_the_vocabulary():
    good = {"status": "fit", "doc": "doc_0123abcd", "bytes": 1, "worst_page_ssim": 0.912345678,
            "lost": ["color"], "in_vault": True, "privacy_posture": "walled", "max_dpi": None}
    assert sanitize(good) == {**good, "worst_page_ssim": 0.9123}


# -- tools ------------------------------------------------------------------

def test_select_document_returns_handle_and_numbers_only(vault, document):
    result = make(vault, document).select_document()
    assert result["status"] == "selected"
    assert result["doc"].startswith("doc_")
    assert result["pages"] == 2 and result["kind"] == "scanned_images" and result["in_vault"] is True
    no_leak(result, str(vault.root))


def test_select_document_outside_vault_is_flagged(vault, tmp_path, scanned):
    outside = tmp_path / "Downloads" / f"{pdfs.SECRET}.pdf"
    outside.parent.mkdir()
    outside.write_bytes(scanned)
    result = make(vault, outside).select_document()
    assert result["in_vault"] is False
    no_leak(result, str(tmp_path))


@pytest.mark.parametrize("state, status", [("cancelled", "cancelled"), ("unavailable", "error:picker_unavailable")])
def test_select_document_cancel_and_unavailable(vault, document, state, status):
    assert make(vault, document, pick=state).select_document() == {"status": status, "privacy_posture": "walled"}


def test_list_vault_has_no_names(vault, document, scanned):
    (vault.outbox / "ignored.pdf").write_bytes(scanned)  # outbox is not listed
    (vault.root / "broken.pdf").write_bytes(b"%PDF-1.7 " + pdfs.SECRET.encode())
    result = make(vault).list_vault()
    assert result["status"] == "listed" and result["count"] == 2
    statuses = sorted(item["status"] for item in result["documents"])
    assert statuses == ["error:unreadable", "ok"]
    no_leak(result, str(vault.root), "broken", "ignored")


def test_compress_happy_path(vault, document, capsys):
    reviewer = Reviewer("approved")
    service = make(vault, document, reviewer=reviewer, ocr_engine=FakeOcr())
    doc = service.select_document()["doc"]
    result = service.compress(doc, "400KB")
    assert result["status"] == "fit"
    assert result["bytes"] <= 400_000 == result["target_bytes"]
    assert result["user_review"] == "approved" and result["saved_to"] == "vault_outbox"
    assert result["ocr_status"] == "compared" and result["ocr_mismatches"] == 0
    assert result["ocr_chars_compared"] > 0
    assert reviewer.calls and reviewer.calls[0]["pages"] == 2
    saved = list(vault.outbox.glob("*.pdf"))
    assert len(saved) == 1 and saved[0].stat().st_size == result["bytes"]
    if os.name != "nt":
        assert saved[0].stat().st_mode & 0o077 == 0  # owner-only
    no_leak(result, str(vault.root))
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("decision", ["rejected", "dismissed"])
def test_rejected_review_saves_nothing(vault, document, decision):
    service = make(vault, document, reviewer=Reviewer(decision))
    result = service.compress(service.select_document()["doc"], "400KB", ocr_check=False)
    assert result["user_review"] == decision and result["saved_to"] == "not_saved"
    assert not list(vault.outbox.glob("*.pdf"))


def test_review_unavailable_still_saves_and_says_so(vault, document):
    service = make(vault, document, reviewer=Reviewer("unavailable"))
    result = service.compress(service.select_document()["doc"], "400KB", ocr_check=False)
    assert result["user_review"] == "unavailable" and result["saved_to"] == "vault_outbox"
    assert result["ocr_status"] == "skipped"


def test_ocr_unavailable(vault, document):
    service = make(vault, document, ocr_engine=None)
    result = service.compress(service.select_document()["doc"], "400KB")
    assert result["ocr_status"] == "unavailable" and result["ocr_chars_compared"] is None


def test_save_dialog(vault, document, tmp_path):
    chosen = tmp_path / "Desktop" / "upload.pdf"
    service = make(vault, document, save_to=chosen)
    result = service.compress(service.select_document()["doc"], "400KB", ocr_check=False, save="dialog")
    assert result["saved_to"] == "user_location" and chosen.is_file()
    no_leak(result, str(tmp_path))


def test_save_dialog_never_overwrites_the_input(vault, document, scanned):
    service = make(vault, document, save_to=document)
    result = service.compress(service.select_document()["doc"], "400KB", ocr_check=False, save="dialog")
    assert result["saved_to"] == "not_saved"
    assert document.read_bytes() == scanned


def test_save_dialog_unavailable_falls_back_to_outbox(vault, document):
    service = make(vault, document, save_state="unavailable")
    result = service.compress(service.select_document()["doc"], "400KB", ocr_check=False, save="dialog")
    assert result["saved_to"] == "vault_outbox"


def test_infeasible(vault, document):
    service = make(vault, document)
    result = service.compress(service.select_document()["doc"], "5KB")
    assert result["status"] == "infeasible" and result["saved_to"] == "not_saved"
    assert result["smallest_bytes"] > 5_000


def test_already_fits_skips_review_and_ocr(vault, document):
    reviewer = Reviewer()
    service = make(vault, document, reviewer=reviewer, ocr_engine=FakeOcr())
    result = service.compress(service.select_document()["doc"], "10MB")
    assert result["status"] == "already_fits"
    assert result["user_review"] == "not_needed" and result["ocr_status"] == "not_needed"
    assert not reviewer.calls


@pytest.mark.parametrize("doc, target, save, status", [
    ("doc_deadbeef", "200KB", "outbox", "error:unknown_doc"),
    ("../../etc/passwd", "200KB", "outbox", "error:unknown_doc"),
    (None, "lots", "outbox", "error:bad_target"),
    (None, "200KB", "/tmp/elsewhere", "error:bad_argument"),
])
def test_bad_arguments(vault, document, doc, target, save, status):
    service = make(vault, document)
    handle = service.select_document()["doc"]
    assert service.compress(doc or handle, target, save=save)["status"] == status


def test_encrypted_document(vault):
    path = vault.root / f"{pdfs.SECRET}.pdf"
    path.write_bytes(pdfs.encrypted_pdf())
    result = make(vault, path).select_document()
    assert result["status"] == "error:encrypted"
    no_leak(result)


def test_deleted_document(vault, document):
    service = make(vault, document)
    handle = service.select_document()["doc"]
    document.unlink()
    assert service.compress(handle, "200KB")["status"] == "error:file_missing"


def test_unexpected_exception_text_never_reaches_the_result(vault, document, monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise RuntimeError(f"object 12 0 R /Title ({pdfs.SECRET}) at {document}")

    monkeypatch.setattr(engine, "compress_bytes", explode)
    service = make(vault, document)
    result = service.compress(service.select_document()["doc"], "200KB")
    assert result == {"status": "error:internal", "privacy_posture": "walled"}
    captured = capsys.readouterr()
    assert pdfs.SECRET not in captured.out + captured.err


def test_privacy_status(vault):
    service = PrivateService(vault, posture_check=lambda v: posture.PostureReport(
        "partial", ["sandbox_disabled"], ["bypass_mode_allowed"]))
    result = service.privacy_status()
    assert result["privacy_posture"] == "partial"
    assert result["missing"] == ["sandbox_disabled"]


def test_handles_are_random_not_derived_from_paths(vault, document):
    first = make(vault, document).select_document()["doc"]
    second = make(vault, document).select_document()["doc"]
    assert first != second  # fresh server, fresh random handle
