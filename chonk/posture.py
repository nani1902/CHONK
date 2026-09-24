"""Check whether Claude Code's settings keep the agent out of the vault.

This is a static reading of the settings files. It is a warning, not
enforcement: Claude Code enforces the rules, and a session started with
``--settings``, ``--setting-sources`` or server-managed settings can differ
from what the files on disk say.

What "walled" requires (per the Claude Code permissions and sandboxing docs):

* a ``Read`` deny rule covering the vault: blocks the Read tool (including PDF
  rendering), Grep/Glob, ``@`` mentions, and the shell file commands Claude
  Code recognises;
* an ``Edit`` deny rule covering the vault;
* ``sandbox.enabled: true`` and a ``sandbox.filesystem.denyRead`` entry
  covering the vault: the operating system blocks every sandboxed shell
  command and its child processes, including a Python script that opens the
  file itself;
* ``sandbox.allowUnsandboxedCommands: false``: closes the
  ``dangerouslyDisableSandbox`` retry;
* no ``sandbox.excludedCommands`` (they run outside the sandbox), no
  ``sandbox.filesystem.disabled``, and no ``allowRead`` entry that re-opens
  the vault;
* a platform the sandbox supports (macOS, Linux, WSL2; not native Windows).
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Posture = Literal["walled", "partial", "open"]

# Fixed vocabulary for what is missing. Order is the order of the fix-up list.
MISSING_CODES = (
    "read_deny_rule",
    "edit_deny_rule",
    "sandbox_disabled",
    "sandbox_denyread",
    "unsandboxed_escape_open",
    "excluded_commands_present",
    "filesystem_isolation_disabled",
    "allowread_reopens_vault",
    "platform_unsupported",
)

EXPLANATIONS = {
    "read_deny_rule": "No Read deny rule covers the vault, so the Read tool can open files in it.",
    "edit_deny_rule": "No Edit deny rule covers the vault, so the agent can write into it.",
    "sandbox_disabled": "The Bash sandbox is off, so any shell command or script can read the vault.",
    "sandbox_denyread": "sandbox.filesystem.denyRead does not list the vault, so the OS does not block scripts.",
    "unsandboxed_escape_open": "allowUnsandboxedCommands is not false, so the agent can ask to run a command outside the sandbox.",
    "excluded_commands_present": "sandbox.excludedCommands is not empty; those commands run outside the sandbox.",
    "filesystem_isolation_disabled": "sandbox.filesystem.disabled is true, which switches off denyRead.",
    "allowread_reopens_vault": "A sandbox.filesystem.allowRead entry re-opens part of the vault.",
    "platform_unsupported": "Native Windows has no Claude Code sandbox; only the deny rules apply there. Use WSL2.",
}

HARDENING = {
    "bypass_mode_allowed": "permissions.disableBypassPermissionsMode is not \"disable\"; a bypass-mode session skips permission prompts.",
}


def default_vault() -> Path:
    configured = os.environ.get("CHONK_VAULT")
    return Path(configured).expanduser() if configured else Path.home() / "Private"


def _posix(path: Path) -> str:
    """Absolute POSIX form as Claude Code matches it: ``C:\\Users\\x`` becomes ``/c/Users/x``."""
    text = path.expanduser().resolve().as_posix()
    match = re.match(r"^([A-Za-z]):/(.*)$", text)
    return f"/{match.group(1).lower()}/{match.group(2)}" if match else text


def display_path(path: Path) -> str:
    """``~/Private`` rather than ``/Users/name/Private`` where possible."""
    try:
        return "~/" + path.expanduser().resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return _posix(path)


def claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def managed_settings_dir() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if sys.platform == "win32":
        return Path(r"C:\Program Files\ClaudeCode")
    return Path("/etc/claude-code")


@dataclass
class SettingsSource:
    scope: Literal["managed", "local", "project", "user"]
    path: Path
    data: dict[str, Any]


def load_sources(project_dir: Path | None = None) -> list[SettingsSource]:
    """Settings in precedence order, highest first."""
    project = project_dir or Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    managed = managed_settings_dir()
    candidates: list[tuple[str, Path]] = [("managed", managed / "managed-settings.json")]
    drop_ins = managed / "managed-settings.d"
    if drop_ins.is_dir():
        # Later drop-ins win over earlier ones; list them highest first.
        for path in sorted(drop_ins.glob("*.json"), reverse=True):
            if not path.name.startswith("."):
                candidates.insert(0, ("managed", path))
    candidates += [
        ("local", project / ".claude" / "settings.local.json"),
        ("project", project / ".claude" / "settings.json"),
        ("user", claude_config_dir() / "settings.json"),
    ]
    sources = []
    seen: set[Path] = set()
    for scope, path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            sources.append(SettingsSource(scope, path, data))  # type: ignore[arg-type]
    return sources


def _get(data: dict[str, Any], *keys: str) -> Any:
    node: Any = data
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _first(sources: list[SettingsSource], *keys: str) -> Any:
    for source in sources:
        value = _get(source.data, *keys)
        if value is not None:
            return value
    return None


def _merged_list(sources: list[SettingsSource], *keys: str) -> list[tuple[SettingsSource, str]]:
    items = []
    for source in sources:
        value = _get(source.data, *keys)
        if isinstance(value, list):
            items += [(source, entry) for entry in value if isinstance(entry, str)]
    return items


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """gitignore-style glob: ``**`` crosses directories, ``*`` does not."""
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _rule_path(rule: str, tool: str) -> str | None:
    match = re.fullmatch(rf"\s*{tool}\((.*)\)\s*", rule)
    return match.group(1) if match else None


def _permission_rule_base(path: str, source: SettingsSource) -> str | None:
    """Absolute POSIX pattern for a Read/Edit rule path, or None if unanchored."""
    home = _posix(Path.home())
    if path.startswith("//"):
        return path[1:]
    if path.startswith("~/"):
        return home + path[1:]
    if path.startswith("/"):
        anchor = source.path.parent if source.scope == "user" else source.path.parent.parent
        return _posix(anchor) + path
    return None  # relative rules match under the current directory only


def _covers(pattern: str, vault: Path) -> bool:
    """Does the glob match files at any depth inside the vault?"""
    root = _posix(vault)
    probes = (root + "/chonk-probe.pdf", root + "/a/b/chonk-probe.pdf")
    regex = _glob_regex(pattern)
    return all(regex.match(probe) for probe in probes)


def _permission_rule_covers(sources: list[SettingsSource], tool: str, vault: Path) -> bool:
    for source, rule in _merged_list(sources, "permissions", "deny"):
        path = _rule_path(rule, tool)
        if path is None:
            if rule.strip() == tool:  # a bare tool name denies the tool everywhere
                return True
            continue
        base = _permission_rule_base(path, source)
        if base and _covers(base, vault):
            return True
    return False


def _sandbox_path(entry: str, source: SettingsSource) -> str:
    home = _posix(Path.home())
    if entry.startswith("~/") or entry == "~":
        return home + entry[1:]
    if entry.startswith("/"):
        return entry
    anchor = claude_config_dir() if source.scope == "user" else source.path.parent.parent
    return _posix(anchor / entry)


def _sandbox_entry_covers(entry: str, source: SettingsSource, vault: Path) -> bool:
    path = _sandbox_path(entry, source).rstrip("/")
    if any(char in path for char in "*?"):
        return _covers(path, vault)
    root = _posix(vault)
    return root == path or root.startswith(path + "/")


def _sandbox_entry_reopens(entry: str, source: SettingsSource, vault: Path) -> bool:
    path = _sandbox_path(entry, source).rstrip("/")
    root = _posix(vault)
    if any(char in path for char in "*?"):
        return bool(_glob_regex(path).match(root + "/chonk-probe.pdf"))
    # An allowRead narrower than (or equal to) the denied vault re-opens it.
    return path == root or path.startswith(root + "/")


@dataclass
class PostureReport:
    posture: Posture
    missing: list[str] = field(default_factory=list)
    hardening: list[str] = field(default_factory=list)
    vault: Path = field(default_factory=default_vault)
    sources: list[Path] = field(default_factory=list)


def check(vault: Path | None = None, project_dir: Path | None = None,
          platform: str | None = None) -> PostureReport:
    vault = (vault or default_vault()).expanduser()
    platform = platform or sys.platform
    sources = load_sources(project_dir)
    missing: list[str] = []

    read_denied = _permission_rule_covers(sources, "Read", vault)
    if not read_denied:
        missing.append("read_deny_rule")
    if not _permission_rule_covers(sources, "Edit", vault):
        missing.append("edit_deny_rule")

    sandbox_on = _first(sources, "sandbox", "enabled") is True
    if not sandbox_on:
        missing.append("sandbox_disabled")
    if not any(_sandbox_entry_covers(entry, source, vault)
               for source, entry in _merged_list(sources, "sandbox", "filesystem", "denyRead")):
        missing.append("sandbox_denyread")
    if _first(sources, "sandbox", "allowUnsandboxedCommands") is not False:
        missing.append("unsandboxed_escape_open")
    if _merged_list(sources, "sandbox", "excludedCommands"):
        missing.append("excluded_commands_present")
    if _first(sources, "sandbox", "filesystem", "disabled") is True:
        missing.append("filesystem_isolation_disabled")
    if any(_sandbox_entry_reopens(entry, source, vault)
           for source, entry in _merged_list(sources, "sandbox", "filesystem", "allowRead")):
        missing.append("allowread_reopens_vault")
    if platform == "win32":
        missing.append("platform_unsupported")

    hardening = []
    if _first(sources, "permissions", "disableBypassPermissionsMode") != "disable":
        hardening.append("bypass_mode_allowed")

    if not missing:
        posture: Posture = "walled"
    elif read_denied or "sandbox_denyread" not in missing:
        posture = "partial"
    else:
        posture = "open"
    return PostureReport(posture, missing, hardening, vault, [s.path for s in sources])


def lock_snippet(vault: Path | None = None) -> dict[str, Any]:
    shown = display_path(vault or default_vault())
    return {
        "permissions": {
            "deny": [f"Read({shown}/**)", f"Edit({shown}/**)"]
            if shown.startswith("~/") else [f"Read(/{shown}/**)", f"Edit(/{shown}/**)"],
            "disableBypassPermissionsMode": "disable",
        },
        "sandbox": {
            "enabled": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"denyRead": [shown]},
        },
    }


def merge_lock(settings: dict[str, Any], snippet: dict[str, Any]) -> dict[str, Any]:
    """Merge the lock into existing settings without dropping anything."""
    merged = json.loads(json.dumps(settings))
    permissions = merged.setdefault("permissions", {})
    deny = permissions.setdefault("deny", [])
    for rule in snippet["permissions"]["deny"]:
        if rule not in deny:
            deny.append(rule)
    permissions["disableBypassPermissionsMode"] = "disable"
    sandbox = merged.setdefault("sandbox", {})
    sandbox["enabled"] = True
    sandbox["allowUnsandboxedCommands"] = False
    filesystem = sandbox.setdefault("filesystem", {})
    deny_read = filesystem.setdefault("denyRead", [])
    for entry in snippet["sandbox"]["filesystem"]["denyRead"]:
        if entry not in deny_read:
            deny_read.append(entry)
    return merged
