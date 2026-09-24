"""Native file dialogs, run in a child process.

The server calls :func:`pick_open` / :func:`pick_save`; they start
``python -m chonk.picker`` and read the chosen path from its stdout. The path
goes to the CHONK server process only. It is never part of a tool result.

A separate process keeps GUI toolkits off the MCP server's event loop (macOS
requires Tk on the main thread) and lets a crashed dialog fail cleanly.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

PickResult = tuple[Literal["selected", "cancelled", "unavailable"], Path | None]

EXIT_SELECTED, EXIT_CANCELLED, EXIT_UNAVAILABLE = 0, 1, 4
TITLE_OPEN = "CHONK: choose a PDF (your AI assistant will not see its name)"
TITLE_SAVE = "CHONK: save the compressed PDF"


def _run(args: list[str], timeout: float | None) -> PickResult:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "chonk.picker", *args],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable", None
    if completed.returncode == EXIT_SELECTED:
        chosen = completed.stdout.decode("utf-8", "surrogateescape").strip()
        return ("selected", Path(chosen)) if chosen else ("cancelled", None)
    if completed.returncode == EXIT_CANCELLED:
        return "cancelled", None
    return "unavailable", None


def pick_open(initial_dir: Path, timeout: float | None = 900) -> PickResult:
    return _run(["open", "--initial-dir", str(initial_dir)], timeout)


def pick_save(initial_dir: Path, default_name: str, timeout: float | None = 900) -> PickResult:
    return _run(["save", "--initial-dir", str(initial_dir), "--default-name", default_name], timeout)


# --------------------------------------------------------------------------
# Child-process side


def _osascript(mode: str, initial_dir: str, default_name: str) -> int:
    folder = f'POSIX file "{_applescript_quote(initial_dir)}"'
    if mode == "open":
        script = (f'POSIX path of (choose file with prompt "{TITLE_OPEN}" '
                  f'of type {{"com.adobe.pdf"}} default location {folder})')
    else:
        script = (f'POSIX path of (choose file name with prompt "{TITLE_SAVE}" '
                  f'default name "{_applescript_quote(default_name)}" default location {folder})')
    completed = subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
    if completed.returncode != 0:
        # osascript exits 1 with error -128 when the user cancels.
        return EXIT_CANCELLED if b"-128" in completed.stderr else EXIT_UNAVAILABLE
    return _emit(completed.stdout.decode("utf-8", "surrogateescape").strip())


def _applescript_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _tk(mode: str, initial_dir: str, default_name: str) -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
    except Exception:
        return EXIT_UNAVAILABLE
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        if mode == "open":
            chosen = filedialog.askopenfilename(
                parent=root, title=TITLE_OPEN, initialdir=initial_dir,
                filetypes=[("PDF documents", "*.pdf"), ("All files", "*")])
        else:
            chosen = filedialog.asksaveasfilename(
                parent=root, title=TITLE_SAVE, initialdir=initial_dir,
                initialfile=default_name, defaultextension=".pdf",
                filetypes=[("PDF documents", "*.pdf")])
    finally:
        root.destroy()
    return _emit(chosen) if chosen else EXIT_CANCELLED


def _zenity(mode: str, initial_dir: str, default_name: str) -> int:
    start = os.path.join(initial_dir, default_name if mode == "save" else "")
    args = ["zenity", "--file-selection", "--filename", start, "--file-filter=PDF | *.pdf *.PDF"]
    if mode == "open":
        args += ["--title", TITLE_OPEN]
    else:
        args += ["--save", "--confirm-overwrite", "--title", TITLE_SAVE]
    completed = subprocess.run(args, capture_output=True, check=False)
    if completed.returncode == 1:
        return EXIT_CANCELLED
    if completed.returncode != 0:
        return EXIT_UNAVAILABLE
    return _emit(completed.stdout.decode("utf-8", "surrogateescape").strip())


def _kdialog(mode: str, initial_dir: str, default_name: str) -> int:
    start = os.path.join(initial_dir, default_name if mode == "save" else "")
    flag = "--getopenfilename" if mode == "open" else "--getsavefilename"
    completed = subprocess.run(["kdialog", flag, start, "*.pdf"], capture_output=True, check=False)
    if completed.returncode == 1:
        return EXIT_CANCELLED
    if completed.returncode != 0:
        return EXIT_UNAVAILABLE
    return _emit(completed.stdout.decode("utf-8", "surrogateescape").strip())


def _emit(path: str) -> int:
    if not path:
        return EXIT_CANCELLED
    sys.stdout.write(path)
    sys.stdout.flush()
    return EXIT_SELECTED


def _has_display() -> bool:
    if os.environ.get("CHONK_NO_GUI"):  # headless sessions (SSH, CI) must never wait on a dialog
        return False
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m chonk.picker")
    parser.add_argument("mode", choices=("open", "save"))
    parser.add_argument("--initial-dir", default=str(Path.home()))
    parser.add_argument("--default-name", default="compressed.pdf")
    args = parser.parse_args(argv)
    if not _has_display():
        return EXIT_UNAVAILABLE
    initial = args.initial_dir if os.path.isdir(args.initial_dir) else str(Path.home())
    if sys.platform == "darwin" and shutil.which("osascript"):
        return _osascript(args.mode, initial, args.default_name)
    backends = [_tk]
    if sys.platform.startswith("linux"):
        if shutil.which("zenity"):
            backends.append(_zenity)
        if shutil.which("kdialog"):
            backends.append(_kdialog)
    for backend in backends:
        code = backend(args.mode, initial, args.default_name)
        if code != EXIT_UNAVAILABLE:
            return code
    return EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
