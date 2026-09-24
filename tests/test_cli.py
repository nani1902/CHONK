from __future__ import annotations

import json
import sys
from pathlib import Path

import pdfs
import pytest

from chonk import posture
from chonk.cli import main


@pytest.fixture
def source(tmp_path: Path, scanned: bytes) -> Path:
    path = tmp_path / f"{pdfs.SECRET}.pdf"
    path.write_bytes(scanned)
    return path


def test_compress_writes_output_and_never_prints_the_input_path(source, capsys):
    assert main(["compress", str(source), "-t", "400KB"]) == 0
    output = source.with_name(source.stem + "-compressed.pdf")
    assert output.is_file() and output.stat().st_size <= 400_000
    captured = capsys.readouterr()
    assert str(source.parent) not in captured.out + captured.err
    assert "Worst page" in captured.out


def test_compress_json(source, tmp_path, capsys):
    out = tmp_path / "out.pdf"
    assert main(["compress", str(source), "-t", "400KB", "-o", str(out), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "fit" and report["bytes"] == out.stat().st_size


def test_refuses_to_overwrite(source, tmp_path, capsys):
    out = tmp_path / "exists.pdf"
    out.write_bytes(b"keep me")
    assert main(["compress", str(source), "-t", "400KB", "-o", str(out)]) == 2
    assert out.read_bytes() == b"keep me"
    assert main(["compress", str(source), "-t", "400KB", "-o", str(source)]) == 2


def test_infeasible_writes_nothing(source, tmp_path):
    out = tmp_path / "never.pdf"
    assert main(["compress", str(source), "-t", "5KB", "-o", str(out)]) == 3
    assert not out.exists()


def test_missing_input_error_has_no_path(tmp_path, capsys):
    missing = tmp_path / f"{pdfs.SECRET}.pdf"
    assert main(["compress", str(missing), "-t", "1MB"]) == 2
    assert pdfs.SECRET not in capsys.readouterr().err


def test_inspect(source, capsys):
    assert main(["inspect", str(source)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "pages": 2, "bytes": source.stat().st_size, "kind": "scanned_images", "signed": False}


def test_doctor_snippet(capsys, tmp_path):
    assert main(["doctor", "--snippet", "--vault", str(tmp_path / "v")]) == 0
    snippet = json.loads(capsys.readouterr().out)
    assert snippet["sandbox"]["allowUnsandboxedCommands"] is False


def test_doctor_apply_backs_up_and_walls(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    config = home / ".claude"
    config.mkdir(parents=True)
    (config / "settings.json").write_text(json.dumps({"model": "keep"}))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(posture, "managed_settings_dir", lambda: tmp_path / "none")
    vault = home / "Private"
    assert main(["doctor", "--privacy", "--vault", str(vault), "--project", str(tmp_path)]) == 1
    assert main(["doctor", "--privacy", "--apply", "--vault", str(vault), "--project", str(tmp_path)]) == 0
    assert json.loads((config / "settings.json").read_text())["model"] == "keep"
    assert list(config.glob("settings.json.chonk-backup-*"))
    capsys.readouterr()
    code = main(["doctor", "--privacy", "--vault", str(vault), "--project", str(tmp_path)])
    output = capsys.readouterr().out
    if sys.platform == "win32":  # no Claude Code sandbox on native Windows: never walled
        assert code == 1 and "PARTIAL" in output and "WSL2" in output
    else:
        assert code == 0 and "WALLED" in output
