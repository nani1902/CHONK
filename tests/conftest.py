"""Test setup. Every test runs with outbound sockets blocked.

This Python-level guard runs everywhere. CI additionally runs the whole suite
inside a Linux network namespace, where the kernel blocks the network too.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import pdfs  # noqa: E402


class NetworkBlocked(RuntimeError):
    pass


_ALLOWED_FAMILIES = {getattr(socket, "AF_UNIX", object())}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guard(method):
        def wrapper(self, address, *args, **kwargs):
            if self.family in _ALLOWED_FAMILIES:
                return method(self, address, *args, **kwargs)
            raise NetworkBlocked(f"test tried to open a network connection to {address!r}")
        return wrapper

    monkeypatch.setattr(socket.socket, "connect", guard(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(real_connect_ex))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(
        NetworkBlocked("test tried to resolve a hostname")))
    yield


@pytest.fixture(scope="session")
def scanned() -> bytes:
    return pdfs.scanned_pdf(title=pdfs.SECRET)


@pytest.fixture(scope="session")
def text_only() -> bytes:
    return pdfs.text_pdf(pages=40)
