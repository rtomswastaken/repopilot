"""Unit tests for ValidationRunner, failure classification, ecosystem discovery, and repair policies."""

import os
from pathlib import Path
import tempfile
import pytest

from repopilot.config.models import ValidationSettings
from repopilot.core.models import ValidationCheck, ValidationResult
from repopilot.execution.limits import ResourceLimits
from repopilot.validation.classifier import FailureCategory, FailureClassifier, FailureReport
from repopilot.validation.discovery import DiscoveredValidation, TestDiscoverer
from repopilot.validation.parser import parse_test_output
from repopilot.validation.policy import PublishingEligibility, RepairLoopController
from repopilot.validation.runner import ValidationRunner


def test_command_success():
    """Verify that a successful test command returns passed=True with exit code 0."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        settings = ValidationSettings(test_command="python -c 'print(\"Tests passed\")'")
        runner = ValidationRunner(root, settings)

        result = runner.validate_task("task-success")
        assert result.passed is True
        assert len(result.checks) == 1
        assert result.checks[0].passed is True
        assert result.checks[0].exit_code == 0
        assert "Tests passed" in result.checks[0].stdout
        assert result.is_publishable is True


def test_command_failure():
    """Verify that a failing test command is detected and classified."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        settings = ValidationSettings(test_command="python -c 'raise AssertionError(\"Test failed: expected 5 but got 4\")'")
        runner = ValidationRunner(root, settings)

        result = runner.validate_task("task-fail")
        assert result.passed is False
        assert result.is_publishable is False
        assert len(result.checks) == 1
        check = result.checks[0]
        assert check.passed is False
        assert check.exit_code != 0
        assert check.failure_category == FailureCategory.TEST_FAILURE.value
        assert result.recoverable is True


def test_execution_timeout():
    """Verify that commands exceeding configured timeouts are terminated and classified as EXECUTION_TIMEOUT."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        # Timeout set to 1 second via limits; command sleeps for 5 seconds
        settings = ValidationSettings(
            test_command="python -c 'import time; time.sleep(5)'",
        )
        limits = ResourceLimits(timeout_seconds=1)
        runner = ValidationRunner(root, settings, limits=limits)


        result = runner.validate_task("task-timeout")
        assert result.passed is False
        assert result.is_publishable is False
        assert len(result.checks) == 1
        check = result.checks[0]
        assert check.timeout is True
        assert check.failure_category == FailureCategory.EXECUTION_TIMEOUT.value
        assert "timed out" in check.stderr.lower()


def test_excessive_command_output_truncation():
    """Verify that excessive stdout/stderr output is truncated to configured limits."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        limits = ResourceLimits(max_output_bytes=200)
        settings = ValidationSettings(
            test_command="python -c 'print(\"A\" * 5000)'",
            timeout_seconds=10,
        )
        runner = ValidationRunner(root, settings, limits=limits)

        result = runner.validate_task("task-loud")
        check = result.checks[0]
        assert "Output truncated to 200 bytes" in check.stdout
        # Bounded length
        assert len(check.stdout.encode("utf-8")) < 500


def test_missing_test_configuration_reported():
    """Verify that repositories with no test commands and no discovered tests are reported clearly and not marked fully validated."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        # Empty repository with no config and no test files
        settings = ValidationSettings(test_command=None, lint_command=None)
        runner = ValidationRunner(root, settings, require_validation=True)

        result = runner.validate_task("task-no-tests")
        assert result.has_tests is False
        assert result.passed is False
        assert result.is_publishable is False
        assert "No test command configured" in (result.error_summary or "")


def test_dependency_failure_classification():
    """Verify that missing dependencies (e.g. ModuleNotFoundError) are distinguished and classified."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        settings = ValidationSettings(test_command="python -c 'import nonexistent_package_xyz'")
        runner = ValidationRunner(root, settings)

        result = runner.validate_task("task-missing-dep")
        assert result.passed is False
        check = result.checks[0]
        assert check.failure_category == FailureCategory.MISSING_DEPENDENCIES.value
        # System dependency failure is not recoverable by simple AI code edits
        assert result.recoverable is False


def test_syntax_error_classification():
    """Verify that syntax errors are classified as SYNTAX_ERROR and marked recoverable."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        settings = ValidationSettings(test_command="python -c 'def invalid_syntax(:'")
        runner = ValidationRunner(root, settings)

        result = runner.validate_task("task-syntax")
        assert result.passed is False
        check = result.checks[0]
        assert check.failure_category == FailureCategory.SYNTAX_ERROR.value
        assert result.recoverable is True


def test_retry_exhaustion_bounded():
    """Verify that bounded repair limits prevent infinite repair loops."""
    controller = RepairLoopController(max_retries=2)
    task_id = "task-repair-budget"

    # Attempt 1: Recoverable test failure
    report1 = FailureReport(
        category=FailureCategory.TEST_FAILURE,
        is_recoverable=True,
        summary="Assertion failed",
    )
    can_retry1, _ = controller.can_attempt_repair(task_id, report1)
    assert can_retry1 is True
    controller.record_attempt(task_id)

    # Attempt 2: Recoverable syntax error
    report2 = FailureReport(
        category=FailureCategory.SYNTAX_ERROR,
        is_recoverable=True,
        summary="SyntaxError",
    )
    can_retry2, _ = controller.can_attempt_repair(task_id, report2)
    assert can_retry2 is True
    controller.record_attempt(task_id)

    # Attempt 3: Retry budget exhausted
    report3 = FailureReport(
        category=FailureCategory.TEST_FAILURE,
        is_recoverable=True,
        summary="Assertion failed again",
    )
    can_retry3, reason = controller.can_attempt_repair(task_id, report3)
    assert can_retry3 is False
    assert "budget exhausted" in reason.lower()

    # Non-recoverable failure rejected immediately regardless of budget
    fresh_task = "task-security"
    report_unrec = FailureReport(
        category=FailureCategory.PROHIBITED_FILE_CHANGE,
        is_recoverable=False,
        summary="Tried to edit .github/workflows",
    )
    can_retry_sec, reason_sec = controller.can_attempt_repair(fresh_task, report_unrec)
    assert can_retry_sec is False
    assert "non-recoverable" in reason_sec.lower()


def test_validation_rejection_prevents_publication():
    """Verify that changes failing validation or security policy cannot be published."""
    # Failed checks result
    failed_result = ValidationResult(
        task_id="t-fail",
        passed=False,
        checks=[ValidationCheck(name="test", passed=False, exit_code=1)],
        has_tests=True,
    )
    eligible, reason = PublishingEligibility.evaluate(failed_result)
    assert eligible is False
    assert "failed" in reason.lower()

    # Passed but repository has no tests
    no_tests_result = ValidationResult(
        task_id="t-notests",
        passed=True,
        checks=[],
        has_tests=False,
    )
    eligible2, reason2 = PublishingEligibility.evaluate(no_tests_result, require_validation=True)
    assert eligible2 is False
    assert "no automated tests" in reason2.lower()

    # Validated result
    passed_result = ValidationResult(
        task_id="t-pass",
        passed=True,
        checks=[ValidationCheck(name="test", passed=True, exit_code=0)],
        has_tests=True,
    )
    eligible3, _ = PublishingEligibility.evaluate(passed_result)
    assert eligible3 is True


def test_ecosystem_discovery_python():
    """Verify auto-discovery of Python repository with tests and linters."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n")
        tests_dir = root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_main.py").write_text("def test_dummy(): pass\n")

        discovered = TestDiscoverer.discover(root)
        assert discovered.ecosystem == "python"
        assert discovered.has_tests is True
        assert discovered.test_command == "pytest"
        assert discovered.lint_command == "ruff check ."
        assert "pyproject.toml" in discovered.discovered_configs


def test_ecosystem_discovery_node():
    """Verify auto-discovery of Node/TypeScript repository scripts."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "package.json").write_text('{"scripts": {"test": "jest", "lint": "eslint ."}}')
        (root / "tsconfig.json").write_text('{}')

        discovered = TestDiscoverer.discover(root)
        assert discovered.ecosystem == "typescript"
        assert discovered.has_tests is True
        assert discovered.test_command == "npm test"
        assert discovered.lint_command == "npm run lint"


def test_parse_test_output():
    """Verify parsing test runner outputs for test counts."""
    # Pytest output
    pytest_out = "======= 5 passed, 2 failed in 0.42s ======="
    stats = parse_test_output(pytest_out)
    assert stats is not None
    assert stats.tests_run == 7
    assert stats.tests_passed == 5
    assert stats.tests_failed == 2

    # Unittest output
    unittest_out = "Ran 10 tests in 0.05s\n\nFAILED (failures=3, errors=1)"
    stats_u = parse_test_output(unittest_out)
    assert stats_u is not None
    assert stats_u.tests_run == 10
    assert stats_u.tests_failed == 4
    assert stats_u.tests_passed == 6
