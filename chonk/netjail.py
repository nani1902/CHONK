"""Run a command with networking removed by the operating system.

CHONK has no network code, but "trust us" is weaker than "the kernel won't let
it". On Linux the command runs in a fresh network namespace (only a downed
loopback interface), using bubblewrap when installed and ``unshare`` from
util-linux otherwise. Filesystem-path Unix sockets still work, so X11 and
Wayland file pickers still open.

macOS (sandbox-exec) and Windows are not implemented yet; :func:`wrap` raises
``NotImplementedError`` there rather than pretending.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def available() -> str | None:
    """Name of the mechanism that would be used, or None."""
    if not sys.platform.startswith("linux"):
        return None
    for tool, probe in (("bwrap", ["bwrap", "--unshare-net", "--dev-bind", "/", "/", "true"]),
                        ("unshare", ["unshare", "--user", "--map-current-user", "--net", "true"]),
                        ("unshare", ["unshare", "-rn", "true"])):
        if shutil.which(tool) is None:
            continue
        try:
            if subprocess.run(probe, capture_output=True, timeout=10, check=False).returncode == 0:
                return " ".join(probe[:-1])
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def wrap(argv: list[str]) -> list[str]:
    mechanism = available()
    if mechanism is None:
        if not sys.platform.startswith("linux"):
            raise NotImplementedError("network isolation is only implemented on Linux")
        raise RuntimeError("neither bwrap nor an unprivileged unshare is usable here")
    return mechanism.split(" ") + ["--", *argv] if mechanism.startswith("unshare") else \
        mechanism.split(" ") + argv
