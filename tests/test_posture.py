from __future__ import annotations

import json
from pathlib import Path

import pytest

from chonk import posture


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CHONK_VAULT", raising=False)
    monkeypatch.setattr(posture, "managed_settings_dir", lambda: tmp_path / "managed")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    return project


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def check(home: Path, project: Path, **kwargs) -> posture.PostureReport:
    return posture.check(home / "Private", project, platform=kwargs.pop("platform", "darwin"))


def test_no_settings_is_open(home, project):
    report = check(home, project)
    assert report.posture == "open"
    assert "read_deny_rule" in report.missing and "sandbox_disabled" in report.missing


def test_the_snippet_walls_the_vault(home, project):
    write(home / ".claude" / "settings.json", posture.lock_snippet(home / "Private"))
    report = check(home, project)
    assert report.posture == "walled", report.missing
    assert report.hardening == []


def test_snippet_uses_home_relative_paths(home):
    snippet = posture.lock_snippet(home / "Private")
    assert snippet["permissions"]["deny"] == ["Read(~/Private/**)", "Edit(~/Private/**)"]
    assert snippet["sandbox"]["filesystem"]["denyRead"] == ["~/Private"]


def test_snippet_for_vault_outside_home_uses_double_slash(home, tmp_path):
    snippet = posture.lock_snippet(tmp_path / "vault")
    assert snippet["permissions"]["deny"][0] == f"Read(/{(tmp_path / 'vault').resolve().as_posix()}/**)"
    assert snippet["permissions"]["deny"][0].startswith("Read(//")


def test_deny_rules_without_sandbox_are_partial(home, project):
    write(home / ".claude" / "settings.json",
          {"permissions": {"deny": ["Read(~/Private/**)", "Edit(~/Private/**)"]}})
    report = check(home, project)
    assert report.posture == "partial"
    assert {"sandbox_disabled", "sandbox_denyread", "unsandboxed_escape_open"} <= set(report.missing)


@pytest.mark.parametrize("mutation, code", [
    (lambda s: s["sandbox"].__setitem__("excludedCommands", ["docker *"]), "excluded_commands_present"),
    (lambda s: s["sandbox"].__setitem__("allowUnsandboxedCommands", True), "unsandboxed_escape_open"),
    (lambda s: s["sandbox"]["filesystem"].__setitem__("disabled", True), "filesystem_isolation_disabled"),
    (lambda s: s["sandbox"]["filesystem"].__setitem__("allowRead", ["~/Private/outbox"]), "allowread_reopens_vault"),
    (lambda s: s["sandbox"].__setitem__("enabled", False), "sandbox_disabled"),
])
def test_each_hole_is_reported(home, project, mutation, code):
    settings = posture.lock_snippet(home / "Private")
    mutation(settings)
    write(home / ".claude" / "settings.json", settings)
    report = check(home, project)
    assert report.posture == "partial" and code in report.missing


def test_native_windows_is_never_walled(home, project):
    write(home / ".claude" / "settings.json", posture.lock_snippet(home / "Private"))
    report = check(home, project, platform="win32")
    assert report.posture == "partial" and report.missing == ["platform_unsupported"]


def test_narrow_rule_does_not_cover_the_vault(home, project):
    settings = posture.lock_snippet(home / "Private")
    settings["permissions"]["deny"] = ["Read(~/Private/*.pdf)", "Edit(~/Private/**)"]
    write(home / ".claude" / "settings.json", settings)
    assert "read_deny_rule" in check(home, project).missing  # misses nested folders


def test_broader_rules_cover_the_vault(home, project):
    settings = posture.lock_snippet(home / "Private")
    settings["permissions"]["deny"] = ["Read(~/**)", "Edit"]
    settings["sandbox"]["filesystem"]["denyRead"] = ["~/"]
    write(home / ".claude" / "settings.json", settings)
    assert check(home, project).posture == "walled"


def test_managed_settings_win_for_booleans(home, project, tmp_path):
    write(home / ".claude" / "settings.json", posture.lock_snippet(home / "Private"))
    write(tmp_path / "managed" / "managed-settings.json", {"sandbox": {"enabled": False}})
    assert "sandbox_disabled" in check(home, project).missing


def test_project_settings_merge_lists(home, project):
    settings = posture.lock_snippet(home / "Private")
    write(home / ".claude" / "settings.json", settings)
    write(project / ".claude" / "settings.local.json", {"sandbox": {"excludedCommands": ["git *"]}})
    assert "excluded_commands_present" in check(home, project).missing


def test_claude_config_dir(home, project, tmp_path, monkeypatch):
    config = tmp_path / "alt-config"
    write(config / "settings.json", posture.lock_snippet(home / "Private"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    assert check(home, project).posture == "walled"


def test_merge_lock_keeps_existing_settings():
    existing = {"model": "x", "permissions": {"deny": ["Bash(rm *)"], "allow": ["Read(./**)"]},
                "sandbox": {"network": {"allowedDomains": ["example.com"]}}}
    merged = posture.merge_lock(existing, posture.lock_snippet(Path.home() / "Private"))
    assert merged["model"] == "x"
    assert merged["permissions"]["allow"] == ["Read(./**)"]
    assert merged["permissions"]["deny"][0] == "Bash(rm *)"
    assert "Read(~/Private/**)" in merged["permissions"]["deny"]
    assert merged["sandbox"]["network"] == {"allowedDomains": ["example.com"]}
    assert merged["sandbox"]["allowUnsandboxedCommands"] is False
    again = posture.merge_lock(merged, posture.lock_snippet(Path.home() / "Private"))
    assert again == merged  # idempotent
