"""Marker for tests that describe required behavior the baseline lacks.

A known-gap test asserts the behavior the product specification requires.
It is expected to fail on the baseline with an AssertionError, and it is
strict: when the gap is fixed the test passes unexpectedly and the run
fails, so the marker must be removed in the change that closes the gap.
"""

from __future__ import annotations

import pytest


def known_gap(task: str, description: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason=f"baseline gap, owned by {task}: {description}",
    )
