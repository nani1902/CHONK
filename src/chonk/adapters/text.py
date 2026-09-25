"""Human-readable text for engine progress events and results."""

from __future__ import annotations

from chonk.inspection import InspectionIssue
from chonk.models import (
    AttemptStarted,
    CandidateMeasured,
    CompressionResult,
    InputInspected,
    ProgressEvent,
    ReasonCode,
    ResultStatus,
)
from chonk.policies import Feature
from chonk.sizes import human_size

FEATURE_LABELS = {
    Feature.ENCRYPTION: "encryption",
    Feature.DIGITAL_SIGNATURE: "a digital signature",
    Feature.INTERACTIVE_FORM: "interactive form fields",
    Feature.XFA_FORM: "an XFA form",
    Feature.ACTIVE_CONTENT: "scripts or other active content",
    Feature.EMBEDDED_FILES: "attached files",
    Feature.ANNOTATIONS: "comments or other annotations",
    Feature.LINKS: "links",
    Feature.OUTLINES: "bookmarks",
    Feature.TAGGED_STRUCTURE: "accessibility tags",
    Feature.OPTIONAL_CONTENT: "layers",
}


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


def _join(labels: list[str]) -> str:
    if len(labels) <= 2:
        return " and ".join(labels)
    return ", ".join(labels[:-1]) + ", and " + labels[-1]


def describe_blocked(result: CompressionResult) -> str:
    """Explain why preflight refused the input. Names features, never content."""
    assert result.status is ResultStatus.BLOCKED
    reasons = set(result.reason_codes)
    decision = result.preflight
    report = result.inspection
    issues = set(report.issues) if report is not None else set()
    lines: list[str] = []

    if ReasonCode.MALFORMED_INPUT in reasons:
        if InspectionIssue.NO_PAGES in issues:
            lines.append("The input PDF has no pages.")
        else:
            lines.append("Could not read input PDF: the file is damaged or is not a PDF.")
    if ReasonCode.ENCRYPTED_INPUT in reasons:
        lines.append(
            "This PDF is encrypted (password-protected). CHONK does not process "
            "encrypted PDFs; save an unencrypted copy first."
        )
    if ReasonCode.SIGNED_INPUT in reasons:
        lines.append(
            "This PDF is digitally signed. Compressing rewrites the file, which "
            "would invalidate the signature."
        )
    if ReasonCode.UNSUPPORTED_FEATURE in reasons and decision is not None:
        unsupported = [
            FEATURE_LABELS[feature]
            for feature in decision.blocking_features
            if feature not in (Feature.ENCRYPTION, Feature.DIGITAL_SIGNATURE)
        ]
        lines.append(
            f"This PDF contains {_join(unsupported)}, which CHONK cannot yet "
            "preserve when compressing."
        )
    if ReasonCode.INSPECTION_INCONCLUSIVE in reasons:
        if InspectionIssue.CONTENT_ENCRYPTED in issues:
            lines.append("Its contents cannot be inspected without the password.")
        else:
            if decision is not None and decision.unknown_features:
                unknown = [FEATURE_LABELS[feature] for feature in decision.unknown_features]
                lines.append(
                    f"CHONK could not rule out {_join(unknown)} in this PDF, so it "
                    "will not compress it."
                )
            if InspectionIssue.RENDERER_UNREADABLE in issues:
                lines.append("The page renderer could not open this PDF.")
            if InspectionIssue.PAGE_COUNT_MISMATCH in issues:
                lines.append("PDF readers disagree about how many pages this PDF has.")
    if not lines:
        lines.append("This PDF is not supported.")
    lines.append("No output was written; the original is unchanged.")
    return "\n".join(lines)
