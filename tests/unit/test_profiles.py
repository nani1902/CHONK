"""Baseline profile ladder and Ghostscript invocation."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_compressor import build_profiles, dpi_steps, make_ghostscript_command


def test_dpi_steps_span_the_range_in_descending_order():
    steps = dpi_steps(600, 72)
    assert steps[0] == 600 and steps[-1] == 72
    assert steps == sorted(steps, reverse=True)
    assert len(steps) == len(set(steps)) == 9


def test_dpi_steps_with_equal_bounds():
    assert dpi_steps(150, 150) == [150]


def test_default_ladder_shape():
    profiles = build_profiles(600, 72)
    assert len(profiles) == 7 + 9 * 7  # source-resolution + 9 dpi steps x 7 QFactors
    first = profiles[0]
    assert first.dpi is None and first.qfactor == 0.15
    ranks = [profile.clarity_rank for profile in profiles]
    assert ranks == sorted(ranks, reverse=True)
    assert profiles[-1].dpi == 72 and profiles[-1].qfactor == 0.97
    assert len({profile.label for profile in profiles}) == len(profiles)
    assert all(72 <= profile.dpi <= 600 for profile in profiles if profile.dpi is not None)


def test_ladder_orders_source_resolution_ties_first():
    """Source resolution and max_dpi share clarity ranks; the stable sort keeps
    the source-resolution profile first within each tie."""
    profiles = build_profiles(600, 72)
    for earlier, later in zip(profiles, profiles[1:]):
        if earlier.clarity_rank == later.clarity_rank:
            assert earlier.dpi is None and later.dpi == 600


@pytest.mark.parametrize("index", [0, 20, -1])
def test_ghostscript_command_is_an_argument_list_with_fixed_safety_flags(index):
    profile = build_profiles(600, 72)[index]
    source = Path("/in dir/source file.pdf")
    output = Path("/out dir/output file.pdf")
    command = make_ghostscript_command("gs", source, output, profile)

    assert command[0] == "gs"
    assert "-dSAFER" in command and "-dBATCH" in command and "-dNOPAUSE" in command
    assert "-sDEVICE=pdfwrite" in command
    assert "-dPreserveAnnots=true" in command
    assert command[-2:] == ["-f", str(source)]  # paths stay single arguments
    assert f"-sOutputFile={output}" in command
    downsample = "true" if profile.dpi is not None else "false"
    assert f"-dDownsampleColorImages={downsample}" in command
    assert f"/QFactor {profile.qfactor:.2f}" in command[command.index("-c") + 1]
