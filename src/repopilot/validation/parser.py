"""Output parsers for extracting structured test counts and statistics from test runners."""

import re
from typing import Optional
from pydantic import BaseModel


class TestExecutionStats(BaseModel):
    """Parsed test counts from runner standard output/error."""
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0


def parse_test_output(output: str) -> Optional[TestExecutionStats]:
    """Parse stdout/stderr to extract structured counts of tests run, passed, and failed."""
    if not output:
        return None

    # 1. Pytest summary line (e.g. "== 5 passed, 2 failed in 0.42s ==")
    # Look for pytest keywords in the output
    if re.search(r"=+\s+.*\b(?:passed|failed|skipped)\b.*in [0-9.]+s\s+=+", output) or re.search(r"\bin [0-9.]+s\b", output):
        passed_m = re.search(r"(\d+)\s+passed", output)
        failed_m = re.search(r"(\d+)\s+failed", output)
        skipped_m = re.search(r"(\d+)\s+skipped", output)
        errors_m = re.search(r"(\d+)\s+error", output)

        if passed_m or failed_m or skipped_m or errors_m:
            passed = int(passed_m.group(1)) if passed_m else 0
            failed = (int(failed_m.group(1)) if failed_m else 0) + (int(errors_m.group(1)) if errors_m else 0)
            skipped = int(skipped_m.group(1)) if skipped_m else 0
            return TestExecutionStats(
                tests_run=passed + failed + skipped,
                tests_passed=passed,
                tests_failed=failed,
                tests_skipped=skipped,
            )

    # 2. Python unittest pattern (e.g. "Ran 12 tests in 0.02s" / "FAILED (failures=2, errors=1)")
    unittest_ran = re.search(r"Ran (\d+) tests? in [0-9.]+s", output)
    if unittest_ran:
        total = int(unittest_ran.group(1))
        fails_match = re.search(r"failures=(\d+)", output)
        errors_match = re.search(r"errors=(\d+)", output)
        fails = int(fails_match.group(1)) if fails_match else 0
        errors = int(errors_match.group(1)) if errors_match else 0
        total_failed = fails + errors
        return TestExecutionStats(
            tests_run=total,
            tests_passed=max(0, total - total_failed),
            tests_failed=total_failed,
        )

    # 3. Jest / Vitest pattern (e.g. "Tests: 2 failed, 8 passed, 10 total")
    jest_match = re.search(r"Tests:\s+(?:(\d+)\s+failed,\s+)?(?:(\d+)\s+passed,\s+)?(\d+)\s+total", output)
    if jest_match:
        failed = int(jest_match.group(1)) if jest_match.group(1) else 0
        passed = int(jest_match.group(2)) if jest_match.group(2) else 0
        total = int(jest_match.group(3)) if jest_match.group(3) else (failed + passed)
        return TestExecutionStats(
            tests_run=total,
            tests_passed=passed,
            tests_failed=failed,
        )

    return None
