"""Human-readable text for engine progress events and results."""

from __future__ import annotations

from chonk.models import (
    AttemptStarted,
    CandidateMeasured,
    CompressionResult,
    InputInspected,
    ProgressEvent,
    ResultStatus,
)
from chonk.sizes import human_size


def describe_progress(event: ProgressEvent) -> str:
    """Return one or more newline-terminated log lines for ``event``."""
    if isinstance(event, InputInspected):
        return (
            f"Input: {event.source} ({human_size(event.source_bytes)}, "
            f"{event.page_count} pages)\n"
            f"Target: at most {human_size(event.target_bytes)}\n"
        )
    if isinstance(event, AttemptStarted):
        return (
            f"Trying profile {event.profile_index + 1}/{event.profile_count}: "
            f"{event.profile.label}\n"
        )
    if isinstance(event, CandidateMeasured):
        attempt = event.attempt
        return (
            f"  {attempt.size_bytes:,} bytes — {attempt.profile.label}; "
            f"render similarity {attempt.visual_similarity:.4%}\n"
        )
    raise TypeError(f"Unknown progress event: {event!r}")


def describe_success(result: CompressionResult) -> str:
    assert result.status is ResultStatus.READY
    assert result.selected is not None
    return (
        f"Saved {result.output}\n"
        f"Size: {human_size(result.selected.size_bytes)} "
        f"(ceiling {human_size(result.target_bytes)})\n"
        f"Profile: {result.selected.profile.label}; {result.attempt_count} attempt(s)\n"
        f"Render similarity: {result.selected.visual_similarity:.4%} "
        f"at {result.comparison_dpi} dpi"
    )


def describe_target_not_met(result: CompressionResult, *, cli_hint: bool) -> str:
    assert result.status is ResultStatus.TARGET_NOT_MET
    hint = (
        "try a lower --min-dpi or a larger --target-size"
        if cli_hint
        else "try a larger maximum size"
    )
    return (
        f"No tested profile reached the size ceiling. The smallest result was "
        f"{human_size(result.smallest.size_bytes)} after {result.attempt_count} attempts; "
        f"{hint}. No output was written."
    )
