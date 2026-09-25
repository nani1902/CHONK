"""Baseline candidate search, driven by scripted candidate sizes.

``choose_profiles`` accepts a ``run_candidate`` callback, so these tests
replace Ghostscript with a table of sizes and similarities. That makes
search behavior exact and fast, including cases real Ghostscript produces
only occasionally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaps import known_gap
from pdf_compressor import Candidate, Profile, choose_profiles


def ladder(count: int) -> list[Profile]:
    return [
        Profile(dpi=None, qfactor=0.15, clarity_rank=1 - i / count, label=f"profile {i}")
        for i in range(count)
    ]


class ScriptedBackend:
    """Returns predetermined sizes; similarity falls with ladder position."""

    def __init__(self, sizes: list[int], similarities: list[float] | None = None):
        self.sizes = sizes
        self.similarities = similarities or [1 - i / 1000 for i in range(len(sizes))]
        self.calls: list[int] = []

    def __call__(self, index: int, profile: Profile) -> Candidate:
        self.calls.append(index)
        return Candidate(
            index, profile, Path(f"candidate-{index}.pdf"), self.sizes[index], self.similarities[index]
        )


def search(sizes, target, max_attempts, similarities=None):
    backend = ScriptedBackend(sizes, similarities)
    best, smallest, attempts = choose_profiles(ladder(len(sizes)), target, max_attempts, backend)
    assert attempts == len(backend.calls) == len(set(backend.calls))
    return best, smallest, backend


def test_first_profile_that_fits_is_returned_immediately():
    best, smallest, backend = search([900, 800, 700], target=1_000, max_attempts=16)
    assert best.profile_index == 0 and smallest.profile_index == 0
    assert backend.calls == [0]


def test_size_equal_to_the_ceiling_fits():
    best, _, _ = search([1_000, 800], target=1_000, max_attempts=16)
    assert best.profile_index == 0 and best.size == 1_000


def test_one_byte_over_the_ceiling_does_not_fit():
    best, _, backend = search([1_001, 800], target=1_000, max_attempts=16)
    assert best.profile_index == 1
    assert backend.calls == [0, 1]


def test_monotonic_ladder_finds_the_fit_boundary():
    sizes = [2_000 - 100 * i for i in range(16)]  # index 10 is the first fit
    best, smallest, backend = search(sizes, target=1_000, max_attempts=16)
    assert best.profile_index == 10
    assert smallest.size == min(sizes[i] for i in backend.calls)
    assert backend.calls[:2] == [0, 15]


def test_selection_prefers_render_similarity_over_ladder_position():
    sizes = [2_000 - 100 * i for i in range(16)]
    similarities = [0.99] * 16
    similarities[12] = 0.999  # a lower-ranked profile that happens to render closer
    best, _, backend = search(sizes, target=1_000, max_attempts=16, similarities=similarities)
    assert 12 in backend.calls
    assert best.profile_index == 12


def test_no_fit_reports_failure_without_selecting():
    best, smallest, _ = search([5_000] * 8, target=1_000, max_attempts=16)
    assert best is None
    assert smallest.size == 5_000


@pytest.mark.parametrize("max_attempts", [2, 3, 4, 5, 8])
@pytest.mark.parametrize(
    "sizes",
    [
        [2_000 - 100 * i for i in range(20)],
        [1_500, 900, 1_500, 1_500, 1_200, 800, 1_100, 700] * 3,
        [1_500] * 19 + [900],
    ],
    ids=["monotonic", "nonmonotonic", "only-last-fits"],
)
def test_attempts_never_exceed_the_budget(sizes, max_attempts):
    _, _, backend = search(sizes, target=1_000, max_attempts=max_attempts)
    assert len(backend.calls) <= max_attempts


def test_any_selected_candidate_fits():
    sizes = [1_500, 900, 1_500, 1_500, 1_200, 800, 1_100, 700] * 3
    for budget in range(2, len(sizes) + 1):
        best, _, _ = search(sizes, target=1_000, max_attempts=budget)
        assert best is None or best.size <= 1_000


# --- Known gaps: behavior CHONK-010 must deliver --------------------------------


@known_gap("CHONK-010", "failure is declared when both endpoints exceed the target")
def test_interior_fit_is_found_when_both_endpoints_exceed_the_target():
    sizes = [1_500, 1_400, 950, 1_300, 1_300, 1_300, 1_300, 1_600]
    best, _, _ = search(sizes, target=1_000, max_attempts=len(sizes))
    assert best is not None and best.size <= 1_000


@known_gap("CHONK-010", "the reported smallest result is the last endpoint, not the minimum")
def test_reported_smallest_is_the_minimum_over_tested_candidates():
    sizes = [1_200, 3_000, 3_000, 3_000, 1_900]
    _, smallest, backend = search(sizes, target=1_000, max_attempts=16)
    assert smallest.size == min(sizes[i] for i in backend.calls)


@known_gap("CHONK-010", "binary search assumes size falls monotonically along the ladder")
def test_full_budget_finds_the_best_fitting_profile_on_a_nonmonotonic_ladder():
    sizes = [1_200, 900] + [1_200] * 6 + [800] * 8  # profile 1 fits and renders best
    best, _, _ = search(sizes, target=1_000, max_attempts=len(sizes))
    assert best.profile_index == 1
