"""Fail if a pytest JUnit report contains any skipped test.

Without Ghostscript the integration tests skip rather than fail, so a
green run could silently test nothing but the unit layer. CI therefore
treats every skip as a failure. Expected failures (``xfail``) appear in the
report as skips of type ``pytest.xfail`` and are allowed: ``xfail_strict``
in ``pytest.ini`` already fails the run if one of them starts passing.

    python .github/scripts/check_no_skips.py report.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    cases = ET.parse(argv[0]).getroot().iter("testcase")
    total = 0
    skipped = []
    for case in cases:
        total += 1
        for skip in case.iter("skipped"):
            if skip.get("type") != "pytest.xfail":
                name = f"{case.get('classname')}::{case.get('name')}"
                skipped.append(f"{name}: {skip.get('message', '')}")
    if total == 0:
        print("::error::the JUnit report contains no tests")
        return 1
    for line in skipped:
        print(f"::error::skipped {line}")
    if skipped:
        print(f"{len(skipped)} of {total} tests were skipped; CI requires all to run.")
        return 1
    print(f"{total} tests, none skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
