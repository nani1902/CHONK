"""The documented supported-feature matrix matches the code that enforces it."""

from __future__ import annotations

import re
from pathlib import Path

from chonk.inspection import BLOCKING_ISSUES, InspectionIssue, PageKind
from chonk.policies import POLICIES, Feature

DOC = Path(__file__).resolve().parents[2] / "docs" / "product" / "SUPPORTED_FEATURES.md"


def table_after(heading: str) -> list[list[str]]:
    """Rows (header first) of the first Markdown table after ``heading``."""
    lines = DOC.read_text(encoding="utf-8").splitlines()
    start = lines.index(heading)
    rows = []
    for line in lines[start + 1:]:
        if line.startswith("|"):
            if not re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line):
                rows.append([cell.strip() for cell in line.strip("|").split("|")])
        elif rows:
            break
    return rows


def code(cell: str) -> str:
    match = re.fullmatch(r"`([a-z_]+)`", cell)
    assert match, cell
    return match.group(1)


def test_feature_matrix_matches_policy_definitions():
    header, *rows = table_after("## 1. Document features")
    assert {code(row[0]) for row in rows} == {feature.value for feature in Feature}
    for policy in POLICIES.values():
        column = header.index(f"`{policy.policy_id}`")
        for row in rows:
            feature = Feature(code(row[0]))
            assert row[column] in ("Blocked", "Allowed"), row
            assert (row[column] == "Blocked") is (feature in policy.blocked_features), (
                policy.policy_id,
                feature,
            )
    documented_policies = {cell.strip("`") for cell in header if cell.startswith("`")}
    assert documented_policies == set(POLICIES)


def test_page_kinds_are_documented():
    _, *rows = table_after("## 2. Page classification")
    assert [code(row[0]) for row in rows] == [kind.value for kind in PageKind]


def test_inspection_issues_and_their_blocking_are_documented():
    _, *rows = table_after("## 3. Inspection issues")
    assert {code(row[0]) for row in rows} == {issue.value for issue in InspectionIssue}
    for row in rows:
        issue = InspectionIssue(code(row[0]))
        blocks_alone = row[1].startswith("Yes")
        expected = issue in BLOCKING_ISSUES or issue in (
            InspectionIssue.UNREADABLE,
            InspectionIssue.NO_PAGES,
        )
        assert blocks_alone is expected, issue
