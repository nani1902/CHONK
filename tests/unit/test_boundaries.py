"""The engine must stay independent of user interfaces and stdout."""

import ast
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = SRC / "chonk"
ADAPTERS = PACKAGE / "adapters"


def test_engine_imports_without_ui_or_mcp():
    code = (
        "import sys\n"
        "import chonk, chonk.engine\n"
        "loaded = [m for m in ('tkinter', '_tkinter', 'mcp') if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    subprocess.run(
        [sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": str(SRC)}
    )


def core_modules():
    return [path for path in PACKAGE.rglob("*.py") if ADAPTERS not in path.parents]


def test_core_modules_do_not_import_cli_or_ui_frameworks():
    forbidden = {"argparse", "tkinter", "mcp"}
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{path}: imports {name}"
            assert not any(n.startswith("chonk.adapters") for n in names), path


def test_core_modules_do_not_print():
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", f"{path}:{node.lineno} calls print()"
