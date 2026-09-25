"""Compression profile ladder and candidate search."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from chonk.models import Profile


@dataclass(frozen=True)
class Candidate:
    profile_index: int
    profile: Profile
    path: Path
    size: int
    visual_similarity: float


def dpi_steps(max_dpi: int, min_dpi: int, count: int = 9) -> list[int]:
    if max_dpi == min_dpi:
        return [max_dpi]
    values = {
        round(max_dpi * ((min_dpi / max_dpi) ** (step / (count - 1))))
        for step in range(count)
    }
    values.update((max_dpi, min_dpi))
    return sorted(values, reverse=True)


def build_profiles(max_dpi: int, min_dpi: int) -> list[Profile]:
    qfactors = (0.15, 0.25, 0.40, 0.55, 0.70, 0.85, 0.97)
    profiles: list[Profile] = [
        Profile(
            dpi=None,
            qfactor=0.15,
            clarity_rank=1.0,
            label="keep source resolution; highest image quality",
        )
    ]

    # Also try higher compression at source resolution before reducing detail.
    for qfactor in qfactors[1:]:
        quality = 1.0 - (qfactor - qfactors[0]) / (qfactors[-1] - qfactors[0])
        profiles.append(
            Profile(
                dpi=None,
                qfactor=qfactor,
                clarity_rank=0.65 + 0.35 * quality,
                label=f"keep source resolution; QFactor {qfactor:.2f}",
            )
        )

    for dpi in dpi_steps(max_dpi, min_dpi):
        dpi_score = math.sqrt(dpi / max_dpi)
        for qfactor in qfactors:
            quality = 1.0 - (qfactor - qfactors[0]) / (qfactors[-1] - qfactors[0])
            profiles.append(
                Profile(
                    dpi=dpi,
                    qfactor=qfactor,
                    clarity_rank=dpi_score * (0.65 + 0.35 * quality),
                    label=f"{dpi} dpi; QFactor {qfactor:.2f}",
                )
            )

    # Higher ranked profiles are attempted first. The score favors resolution
    # while still accounting for the image-compression setting.
    profiles.sort(key=lambda item: item.clarity_rank, reverse=True)

    # The source-resolution profile is the least destructive and always leads.
    source_profile = profiles.pop(next(i for i, p in enumerate(profiles) if p.dpi is None and p.qfactor == 0.15))
    profiles.insert(0, source_profile)
    return profiles


def choose_profiles(
    profiles: Sequence[Profile],
    target_size: int,
    max_attempts: int,
    run_candidate: Callable[[int, Profile], Candidate],
) -> tuple[Candidate | None, Candidate, int]:
    """Binary-search the ordered clarity ladder, then inspect nearby profiles."""
    results: dict[int, Candidate] = {}

    def attempt(index: int) -> Candidate | None:
        if index in results:
            return results[index]
        if len(results) >= max_attempts:
            return None
        candidate = run_candidate(index, profiles[index])
        results[index] = candidate
        return candidate

    highest_fidelity = attempt(0)
    assert highest_fidelity is not None
    if highest_fidelity.size <= target_size:
        return highest_fidelity, highest_fidelity, len(results)

    lowest_fidelity = attempt(len(profiles) - 1)
    if lowest_fidelity is None:
        return None, highest_fidelity, len(results)
    if lowest_fidelity.size > target_size:
        return None, lowest_fidelity, len(results)

    best_fit_index = len(profiles) - 1
    best_nonfit_index = 0
    while best_fit_index - best_nonfit_index > 1 and len(results) < max_attempts:
        midpoint = (best_fit_index + best_nonfit_index) // 2
        candidate = attempt(midpoint)
        if candidate is None:
            break
        if candidate.size <= target_size:
            best_fit_index = midpoint
        else:
            best_nonfit_index = midpoint

    # Check nearby profiles to catch small non-monotonic size changes between
    # Ghostscript configurations.
    neighbors = [
        best_fit_index - 2,
        best_fit_index - 1,
        best_fit_index + 1,
        best_fit_index + 2,
    ]
    for index in neighbors:
        if 0 <= index < len(profiles) and len(results) < max_attempts:
            attempt(index)

    fitting = [candidate for candidate in results.values() if candidate.size <= target_size]
    best = max(
        fitting,
        key=lambda candidate: (candidate.visual_similarity, -candidate.profile_index),
    )
    smallest = min(results.values(), key=lambda candidate: candidate.size)
    return best, smallest, len(results)
