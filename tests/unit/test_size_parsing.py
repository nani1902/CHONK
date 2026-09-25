"""Baseline size-ceiling parsing: decimal and binary units, exact bytes."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

import pytest

from pdf_compressor import human_size, parse_size

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("500KB", 500_000),
        ("5MB", 5_000_000),
        ("2MB", 2_000_000),
        ("1GB", 1_000_000_000),
        ("1KiB", 1_024),
        ("5MiB", 5_242_880),
        ("4.5MiB", 4_718_592),
        ("1GiB", 1_073_741_824),
        ("1.5MB", 1_500_000),
        ("1200000B", 1_200_000),
        ("1200000", 1_200_000),
        ("1", 1),
    ],
)
def test_units_are_exact_byte_counts(text, expected):
    assert parse_size(text) == expected


def test_decimal_and_binary_units_differ():
    assert parse_size("1MB") == 1_000_000
    assert parse_size("1MiB") == 1_048_576
    assert parse_size("1MiB") - parse_size("1MB") == 48_576


@pytest.mark.parametrize("text", ["2 MB", " 2mb ", "2Mb", "2mB", "2\tMB"])
def test_units_are_case_and_space_insensitive(text):
    assert parse_size(text) == 2_000_000


@pytest.mark.parametrize(
    "text",
    ["", "MB", "0", "0KB", "0.0001B", "-1MB", "+1MB", "1e6", "1,000KB", "5 megabytes", "5TB", ".5MB", "5.MB"],
)
def test_invalid_or_nonpositive_sizes_are_rejected(text):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size(text)


def test_baseline_truncates_fractional_bytes():
    """Captured baseline: sub-byte precision is dropped, not rejected."""
    assert parse_size("1.9B") == 1
    assert parse_size("1.0005KB") == 1_000


def test_baseline_accepts_values_with_no_upper_bound():
    """Captured baseline: CHONK-004 is expected to add bounded numeric limits."""
    assert parse_size("99999999999999999999GB") > 10**28


@pytest.mark.parametrize(
    ("text", "expected"),
    [("4.1MB", 4_100_000), ("8.2MB", 8_200_000), ("2.05MB", 2_050_000), ("2.01KB", 2_010)],
)
def test_short_decimal_sizes_are_exact(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (999, "999 bytes"),
        (1_024, "1.00 KiB (1,024 bytes)"),
        (2_000_000, "1.91 MiB (2,000,000 bytes)"),
        (1_073_741_824, "1.00 GiB (1,073,741,824 bytes)"),
    ],
)
def test_human_size_reports_binary_units_and_exact_bytes(size, expected):
    assert human_size(size) == expected


def test_desktop_app_uses_the_same_parser():
    """app.py needs Tkinter, so inspect it statically instead of importing it."""
    tree = ast.parse((REPO_ROOT / "app.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in ("chonk", "pdf_compressor")
        for alias in node.names
    }
    assert "parse_size" in imported
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "parse_size" in calls
