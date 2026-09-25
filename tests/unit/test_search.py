from pathlib import Path

from chonk.search import Candidate, build_profiles, choose_profiles, dpi_steps


def test_default_ladder_shape():
    profiles = build_profiles(600, 72)
    assert len(profiles) == 70
    first = profiles[0]
    assert first.dpi is None and first.qfactor == 0.15
    ranks = [profile.clarity_rank for profile in profiles[1:]]
    assert ranks == sorted(ranks, reverse=True)
    assert dpi_steps(600, 72)[0] == 600 and dpi_steps(600, 72)[-1] == 72
    assert dpi_steps(150, 150) == [150]


def run_with_sizes(sizes, target, max_attempts=16, similarity=None):
    profiles = build_profiles(600, 72)[: len(sizes)]
    tried = []

    def run_candidate(index, profile):
        tried.append(index)
        score = similarity(index) if similarity else 1.0 - index / 1000
        return Candidate(index, profile, Path(f"c{index}.pdf"), sizes[index], score)

    best, smallest, count = choose_profiles(profiles, target, max_attempts, run_candidate)
    return best, smallest, count, tried


def test_returns_first_profile_when_it_fits(capsys):
    best, smallest, count, tried = run_with_sizes([100, 90, 80], target=100)
    assert tried == [0] and count == 1
    assert best.profile_index == 0 and smallest.profile_index == 0
    assert capsys.readouterr() == ("", "")


def test_binary_search_selects_highest_similarity_fit():
    sizes = [1000 - 10 * i for i in range(40)]
    best, smallest, count, tried = run_with_sizes(sizes, target=700)
    assert best.size <= 700
    assert best.profile_index == 30  # First fitting index; similarity falls with index.
    assert smallest.profile_index == 39
    assert count == len(set(tried)) <= 16


def test_endpoint_failure_reports_target_not_met():
    sizes = [1000] * 10
    best, smallest, count, tried = run_with_sizes(sizes, target=500)
    assert best is None
    assert tried == [0, 9] and count == 2
    assert smallest.profile_index == 9


def test_respects_attempt_budget():
    sizes = [1000 - i for i in range(70)]
    best, _, count, tried = run_with_sizes(sizes, target=935, max_attempts=4)
    assert count == 4 and len(tried) == 4
    assert best is not None and best.size <= 935
