"""The synthetic corpus is reproducible, synthetic, and what it claims to be."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PyPdfError

from corpus import BY_NAME, FIXTURES, SYNTHETIC_MARKER, fixture_bytes
from corpus.catalog import OWNER_PASSWORD, USER_PASSWORD
from corpus.signing import SIGNER_COMMON_NAME, certificate_pem, signer
from evaluation import annotation_subtypes, covering_signatures, form_fields, outline_count

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATION_FAMILIES = {
    "Exact size",
    "Ordinary text",
    "Scans",
    "Local detail",
    "Feature inventory",
    "Structural damage",
    "Text damage",
    "Search",
    "Already fits",
    "Filesystem",
    "Runtime",
}


def reader(name: str) -> PdfReader:
    return PdfReader(io.BytesIO(fixture_bytes(name)), strict=False)


@pytest.mark.parametrize("spec", FIXTURES, ids=lambda spec: spec.name)
def test_every_fixture_is_byte_for_byte_reproducible(spec):
    assert spec.build() == spec.build()


def test_reproducible_in_a_fresh_interpreter():
    """Guards against hidden dependence on hash seeds, clocks, or process state."""
    script = (
        "import hashlib, sys; sys.path[:0] = ['tests', '.'];"
        "from corpus import FIXTURES;"
        "print(hashlib.sha256(b''.join(s.build() for s in FIXTURES)).hexdigest())"
    )
    digests = {
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={"PYTHONHASHSEED": str(seed), "PATH": ""},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in (1, 2)
    }
    assert len(digests) == 1


def test_catalog_records_intent_and_provenance():
    assert len(BY_NAME) == len(FIXTURES)
    for spec in FIXTURES:
        assert re.fullmatch(r"[a-z0-9-]+", spec.name)
        assert spec.family in VALIDATION_FAMILIES
        assert spec.intent and spec.baseline and spec.features
        assert spec.build.__doc__ or spec.build.__name__.startswith("build_")


def test_the_repository_contains_no_committed_documents():
    """Fixtures are generated at test time; binary documents stay out of Git."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")
    tracked = subprocess.run([git, "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True)
    if tracked.returncode != 0:
        pytest.skip("not a Git checkout")
    suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".p12", ".pfx", ".key", ".pem"}
    offenders = [line for line in tracked.stdout.splitlines() if Path(line).suffix.lower() in suffixes]
    assert offenders == []


@pytest.mark.parametrize(
    "spec", [s for s in FIXTURES if s.has_text_layer and "malformed" not in s.features],
    ids=lambda spec: spec.name,
)
def test_every_text_page_carries_the_synthetic_marker(spec):
    document = reader(spec.name)
    if document.is_encrypted:
        document.decrypt(USER_PASSWORD if "user-password" in spec.name else "")
    for page in document.pages:
        assert SYNTHETIC_MARKER in page.extract_text()


def test_required_fixture_classes_are_present():
    features = {feature for spec in FIXTURES for feature in spec.features}
    for required in (
        "text-layer", "image-only", "rgb-image", "small-text", "multi-page", "encrypted",
        "acroform", "annotations", "signature", "malformed",
    ):
        assert required in features
    assert "already-small" in BY_NAME


def test_text_fixture_has_extractable_text_and_scan_does_not():
    assert "Total due:" in reader("text-statement").pages[0].extract_text()
    scan = reader("image-scan")
    assert len(scan.pages) == 2
    assert all(not (page.extract_text() or "").strip() for page in scan.pages)


def test_mixed_fixture_has_text_and_an_image():
    page = reader("image-mixed").pages[0]
    assert "Inspection number" in page.extract_text()
    assert len(page.images) == 1


def test_multipage_fixture_has_distinct_pages_and_geometry():
    document = reader("text-multipage")
    texts = [page.extract_text() for page in document.pages]
    assert len(document.pages) == 12 and len(set(texts)) == 12
    sizes = {(round(float(p.mediabox.width)), round(float(p.mediabox.height))) for p in document.pages}
    assert sizes == {(612, 792), (595, 842), (792, 612)}
    assert [page.rotation for page in document.pages].count(90) == 1
    assert outline_count(fixture_bytes("text-multipage")) == 2


@pytest.mark.parametrize(
    ("name", "password", "needs_password"),
    [("encrypted-user-password", USER_PASSWORD, True), ("encrypted-owner-only", "", False)],
)
def test_encrypted_fixtures(name, password, needs_password):
    document = reader(name)
    assert document.is_encrypted
    if needs_password:
        with pytest.raises(FileNotDecryptedError):
            document.pages[0].extract_text()
    assert document.decrypt(password) != 0
    assert "Balance 12.34" in document.pages[0].extract_text()
    owner = reader(name)
    assert owner.decrypt(OWNER_PASSWORD) != 0


def test_form_fixture_has_filled_fields():
    assert form_fields(fixture_bytes("form-acroform")) == {
        "applicant_name": "Synthetic Person",
        "consent_synthetic": "/Yes",
    }


def test_annotation_fixture():
    assert annotation_subtypes(fixture_bytes("annotations")) == [["/Highlight", "/Link", "/Text"]]


def test_signature_fixture_covers_the_whole_file():
    assert covering_signatures(fixture_bytes("signed-pkcs7")) == 1


def test_signer_is_a_disposable_test_identity():
    _, certificate = signer()
    assert SIGNER_COMMON_NAME in certificate.subject.rfc4514_string()
    assert certificate.issuer == certificate.subject  # self-signed, trusted by nobody


def test_signature_verifies_with_openssl(tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed")
    data = fixture_bytes("signed-pkcs7")
    start, first, second, rest = (
        int(value)
        for value in re.search(rb"/ByteRange \[(\d+) (\d+) (\d+) (\d+)\]", data).groups()
    )
    (tmp_path / "content.bin").write_bytes(data[start:first] + data[second : second + rest])
    (tmp_path / "signature.der").write_bytes(bytes.fromhex(data[first + 1 : second - 1].decode()))
    (tmp_path / "signer.pem").write_bytes(certificate_pem())
    result = subprocess.run(
        [
            openssl, "cms", "-verify", "-binary", "-inform", "DER",
            "-in", "signature.der", "-content", "content.bin",
            "-CAfile", "signer.pem", "-purpose", "any", "-out", "/dev/null",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["malformed-truncated", "malformed-not-pdf"])
def test_unreadable_fixtures_are_unreadable(name):
    with pytest.raises(PyPdfError):
        len(reader(name).pages)


def test_bad_xref_fixture_is_repairable():
    data = fixture_bytes("malformed-bad-xref")
    assert data != fixture_bytes("text-statement")
    assert len(reader("malformed-bad-xref").pages) == 1


def test_manifest_records_every_fixture(tmp_path):
    import json

    from corpus.__main__ import write_corpus

    manifest = json.loads(write_corpus(tmp_path).read_text(encoding="utf-8"))
    assert manifest["contains_real_data"] is False
    assert [entry["name"] for entry in manifest["fixtures"]] == [s.name for s in FIXTURES]
    for entry in manifest["fixtures"]:
        data = (tmp_path / entry["file"]).read_bytes()
        assert entry["bytes"] == len(data)
        assert entry["generator"].startswith("tests/corpus/catalog.py:build_")
