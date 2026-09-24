"""No network code, OS-level network isolation, and the out-of-process windows."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from chonk import netjail, picker, review

PACKAGE = Path(__file__).resolve().parents[1] / "chonk"
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "httpx2", "aiohttp",
                   "ftplib", "smtplib", "telnetlib", "xmlrpc", "websockets", "asyncio.streams"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("module", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_chonk_has_no_network_code(module: Path):
    imported = imported_modules(module)
    offending = {name for name in imported if name.split(".")[0] in NETWORK_MODULES or name in NETWORK_MODULES}
    assert not offending, f"{module.name} imports {offending}"


PROBE = "import socket; s = socket.socket(); s.settimeout(3); s.connect(('1.1.1.1', 53))"


@pytest.mark.skipif(netjail.available() is None, reason="no bwrap or unprivileged unshare here")
def test_netjail_blocks_the_network():
    completed = subprocess.run(netjail.wrap([sys.executable, "-c", PROBE]), capture_output=True, timeout=30)
    assert completed.returncode != 0
    assert b"unreachable" in completed.stderr.lower() or b"errno" in completed.stderr.lower()


def test_netjail_refuses_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(NotImplementedError):
        netjail.wrap(["true"])


@pytest.fixture
def headless(monkeypatch):
    if sys.platform in ("darwin", "win32"):
        pytest.skip("these platforms always have a display")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def test_review_is_unavailable_without_a_display(headless):
    png = b"\x89PNG\r\n\x1a\n"
    decision = review.request_review(png, png, page=0, pages=1, ssim=0.9, output_bytes=1, target_bytes=2)
    assert decision == "unavailable"


def test_review_protocol_round_trip():
    import io

    blob = review._pack(b"abc") + review._pack(b"")
    stream = io.BytesIO(blob)
    assert review._unpack(stream) == b"abc" and review._unpack(stream) == b""
    with pytest.raises(EOFError):
        review._unpack(stream)


def test_picker_is_unavailable_without_a_display(headless, tmp_path):
    assert picker.pick_open(tmp_path) == ("unavailable", None)


def test_windows_run_in_child_processes_not_the_server():
    # The server must never import a GUI toolkit itself.
    for name in ("private.py", "mcp_server.py", "engine.py", "quality.py", "vault.py"):
        assert not any(m.startswith("tkinter") for m in imported_modules(PACKAGE / name)), name
    assert os.path.exists(PACKAGE / "review.py") and os.path.exists(PACKAGE / "picker.py")
