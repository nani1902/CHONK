"""The desktop launcher consumes engine events and results, not redirected output."""

from __future__ import annotations

import queue
import sys
from pathlib import Path

import pytest

pytest.importorskip("tkinter")

import app  # noqa: E402
from chonk import CompressionError, ResultStatus  # noqa: E402
from conftest import PaddingBackend  # noqa: E402


class Var:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class Widget:
    def stop(self):
        pass

    def configure(self, **_kwargs):
        pass


def headless_app() -> app.ChonkApp:
    instance = app.ChonkApp.__new__(app.ChonkApp)
    instance.messages = queue.Queue()
    instance.status = Var()
    instance.progress = Widget()
    instance.compress_button = Widget()
    instance.logged = []
    instance._append_log = instance.logged.append
    return instance


def drain(instance: app.ChonkApp) -> list:
    items = []
    while not instance.messages.empty():
        items.append(instance.messages.get_nowait())
    return items


def run_worker(monkeypatch, instance, image_pdf, output, target, padding):
    real = app.compress_pdf
    monkeypatch.setattr(
        app,
        "compress_pdf",
        lambda request, on_progress: real(
            request, backend=PaddingBackend(padding), on_progress=on_progress
        ),
    )
    instance._compress_worker(image_pdf, output, target, False)
    return drain(instance)


def test_worker_success_reports_typed_events(monkeypatch, image_pdf, unpadded_size, tmp_path, capfd):
    instance = headless_app()
    output = tmp_path / "out.pdf"
    messages = run_worker(
        monkeypatch, instance, image_pdf, output, unpadded_size + 60_000,
        lambda profile: int(profile.clarity_rank * 100_000),
    )
    assert capfd.readouterr() == ("", "")
    assert {kind for kind, _ in messages} == {"progress", "done"}
    kind, result = messages[-1]
    assert kind == "done" and result.status is ResultStatus.READY
    for message in messages:
        instance.messages.put(message)
    instance.root = type("Root", (), {"after": lambda *_args: None})()
    instance._drain_messages()
    assert instance.status.value.startswith("Done — ")
    assert any(line.startswith("Trying profile 1/70") for line in instance.logged)
    assert any(line.startswith(f"Saved {output.resolve()}") for line in instance.logged)


def test_worker_target_not_met_is_distinct_from_errors(monkeypatch, image_pdf, unpadded_size, tmp_path):
    instance = headless_app()
    messages = run_worker(
        monkeypatch, instance, image_pdf, tmp_path / "out.pdf", unpadded_size,
        lambda profile: int(profile.clarity_rank * 100_000),
    )
    result = messages[-1][1]
    assert result.status is ResultStatus.TARGET_NOT_MET
    instance._finish(result)
    assert instance.status.value == "Could not meet the requested size limit."
    assert "--min-dpi" not in instance.logged[-1]

    failing = headless_app()

    def fail(request, on_progress):
        raise CompressionError("Ghostscript was not found.")

    monkeypatch.setattr(app, "compress_pdf", fail)
    failing._compress_worker(image_pdf, tmp_path / "out.pdf", 1_000, False)
    messages = drain(failing)
    assert messages == [("log", "Error: Ghostscript was not found.\n"), ("done", None)]
    failing._finish(None)
    assert failing.status.value == "Compression failed; see the details below."
