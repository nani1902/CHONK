import argparse

import pytest

from pdf_compressor import parse_size


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2MB", 2_000_000),
        ("2MiB", 2_097_152),
        ("4.1MB", 4_100_000),
        ("1.15GB", 1_150_000_000),
        ("0.29MB", 290_000),
        ("500KB", 500_000),
        ("1.5KiB", 1_536),
        ("1GiB", 1_073_741_824),
        ("1200000B", 1_200_000),
        ("1200000", 1_200_000),
        ("1.0MB", 1_000_000),
        ("2kB", 2_000),
        # All-lowercase units stay bytes, matching common portal spelling.
        ("2 mb", 2_000_000),
        ("2kb", 2_000),
        ("2gb", 2_000_000_000),
        ("2mib", 2_097_152),
        ("2b", 2),
    ],
)
def test_accepts_byte_units(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2 MB", 2_000_000),
        (" 2MB", 2_000_000),
        ("2MB ", 2_000_000),
        ("  2  MB  ", 2_000_000),
        ("\t2MB\n", 2_000_000),
        ("4.1 MB", 4_100_000),
        ("2 MiB", 2_097_152),
    ],
)
def test_ignores_whitespace_around_number_and_unit(value, expected):
    assert parse_size(value) == expected


@pytest.mark.parametrize("value", ["2.5B", "2.5", "0.0001KB", "0.001KiB"])
def test_rejects_fractional_bytes(value):
    with pytest.raises(argparse.ArgumentTypeError, match="whole number of bytes"):
        parse_size(value)


def test_fractional_byte_message_is_exact_beyond_default_precision():
    with pytest.raises(argparse.ArgumentTypeError) as excinfo:
        parse_size("1.0000000000000000000000000001GB")
    assert "1000000000.0000000000000000001 bytes" in str(excinfo.value)


@pytest.mark.parametrize("value", ["0MB", "0", "0.0KiB", "0B"])
def test_rejects_zero(value):
    with pytest.raises(argparse.ArgumentTypeError, match="greater than zero"):
        parse_size(value)


@pytest.mark.parametrize(
    ("value", "suggestion"),
    [
        ("2 Mb", "use MB for megabytes"),
        ("2Kb", "use KB for kilobytes"),
        ("2Gb", "use GB for gigabytes"),
        ("2Mib", "use MiB for mebibytes"),
        ("2 Mbit", "use MB for megabytes"),
        ("2mbit", "use MB for megabytes"),
        ("16bit", "use B for bytes"),
        ("16 bits", "use B for bytes"),
    ],
)
def test_rejects_bit_units_with_explanation(value, suggestion):
    with pytest.raises(argparse.ArgumentTypeError) as excinfo:
        parse_size(value)
    message = str(excinfo.value)
    assert "bits" in message
    assert "1 byte = 8 bits" in message
    assert suggestion in message


@pytest.mark.parametrize("value", ["2TB", "2 Tb", "2 bytes", "2XB"])
def test_rejects_unsupported_units(value):
    with pytest.raises(argparse.ArgumentTypeError, match="supported units"):
        parse_size(value)


@pytest.mark.parametrize("value", ["", "   ", "MB", "-2MB", ".5MB", "2.MB", "1,000KB", "1e6"])
def test_rejects_malformed_input(value):
    with pytest.raises(argparse.ArgumentTypeError, match="use a size such as"):
        parse_size(value)


def test_rejects_non_ascii_digits():
    with pytest.raises(argparse.ArgumentTypeError):
        parse_size("٢MB")  # ARABIC-INDIC DIGIT TWO
