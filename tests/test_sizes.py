"""Size-limit parsing and display, per docs/decisions/0002-size-limit-units.md."""

import argparse

import pytest

from pdf_compressor import build_parser, human_size, parse_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2MB", 2_000_000),
        ("2 MB", 2_000_000),
        ("  2MB  ", 2_000_000),
        ("2mb", 2_000_000),
        ("2kB", 2_000),
        ("2MiB", 2_097_152),
        ("2 KIB", 2_048),
        ("4.1MB", 4_100_000),
        ("1.15GB", 1_150_000_000),
        ("0.29MB", 290_000),
        ("4.5MiB", 4_718_592),
        ("1200000", 1_200_000),
        ("1200000B", 1_200_000),
        ("1B", 1),
    ],
)
def test_parse_size_is_exact(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2.5B", "whole number of bytes"),
        ("0.0001KB", "whole number of bytes"),
        ("0MB", "greater than zero"),
        ("0", "greater than zero"),
        ("2 Mb", "bits"),
        ("2Kb", "bits"),
        ("2Gb", "bits"),
        ("2Kib", "bits"),
        ("2 Mbit", "bits"),
        ("16bits", "bits"),
        ("2XB", "supported units"),
        ("", "use a size"),
        ("-2MB", "use a size"),
        ("2 M B", "use a size"),
    ],
)
def test_parse_size_rejects_ambiguous_or_invalid_values(value, message):
    with pytest.raises(argparse.ArgumentTypeError, match=message):
        parse_size(value)


def test_cli_reports_invalid_target_size(capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["input.pdf", "--target-size", "2 Mb"])
    assert exit_info.value.code == 2
    assert "bits" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (512, "512 bytes"),
        (2_000_000, "2.00 MB / 1.90 MiB (2,000,000 bytes)"),
        (2_097_152, "2.09 MB / 2.00 MiB (2,097,152 bytes)"),
        (1_000, "1.00 KB (1,000 bytes)"),
        (999_999, "999.99 KB / 976.56 KiB (999,999 bytes)"),
        (1_150_000_000, "1.15 GB / 1.07 GiB (1,150,000,000 bytes)"),
    ],
)
def test_human_size_shows_decimal_binary_and_exact_bytes(size, expected):
    assert human_size(size) == expected


def test_human_size_never_overstates_size():
    # Rounding would display 1,999,999 bytes as "2.00 MB", equal to a 2 MB ceiling.
    assert human_size(1_999_999).startswith("1.99 MB")
