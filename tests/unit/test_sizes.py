import argparse

import pytest

from chonk.adapters.cli import parse_size as parse_size_arg
from chonk.sizes import human_size, parse_size


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1200000B", 1_200_000),
        ("1200000", 1_200_000),
        ("500KB", 500_000),
        ("2MB", 2_000_000),
        ("2 mb", 2_000_000),
        (" 1GB ", 1_000_000_000),
        ("1KiB", 1_024),
        ("5MiB", 5_242_880),
        ("1GiB", 1_073_741_824),
        # Binary floating point would give 4,099,999 and 2,099,999 bytes.
        ("4.1MB", 4_100_000),
        ("2.1MB", 2_100_000),
        ("4.5MiB", 4_718_592),
        # Fractional bytes round down so the ceiling never grows.
        ("1.9B", 1),
        ("0.0015KB", 1),
    ],
)
def test_parse_size_exact_bytes(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "MB", "-1MB", "1e6", "2 M B", "1,000KB"])
def test_parse_size_rejects_malformed(text):
    with pytest.raises(ValueError, match="use a size such as"):
        parse_size(text)


def test_parse_size_rejects_unknown_unit():
    with pytest.raises(ValueError, match="supported units"):
        parse_size("3XB")


@pytest.mark.parametrize("text", ["0", "0MB", "0.4B"])
def test_parse_size_rejects_zero(text):
    with pytest.raises(ValueError, match="greater than zero"):
        parse_size(text)


def test_cli_wrapper_reports_argparse_error():
    with pytest.raises(argparse.ArgumentTypeError, match="supported units"):
        parse_size_arg("3XB")


def test_human_size():
    assert human_size(999) == "999 bytes"
    assert human_size(2_000_000) == "1.91 MiB (2,000,000 bytes)"
    assert human_size(1_024) == "1.00 KiB (1,024 bytes)"
