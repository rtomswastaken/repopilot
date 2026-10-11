"""Automated validation runner for tests and linting in isolated workspaces."""

import logging
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Dict, List, Optional

from repopilot.config.models import ValidationSettings
from repopilot.core.models import ValidationCheck, ValidationResult
from repopilot.execution.limits import ResourceLimits, setup_posix_limits, truncate_output
from repopilot.execution.security import SecretSanitizer
from repopilot.validation.classifier import FailureCategory, FailureClassifier
from repopilot.validation.discovery import TestDiscoverer
from repopilot.validation.parser import parse_test_output
from repopilot.validation.policy import PublishingEligibility

logger = logging.getLogger(__name__)


class ValidationRunner:
    """Executes tests and linters in the isolated workspace with security boundaries and resource bounds."""

    def __init__(
        self,
        workspace_root: Path,
        settings: ValidationSettings,
        limits: Optional[ResourceLimits] = None,
        require_validation: bool = True,
    ):
        self.workspace_root = workspace_root.resolve()
        self.settings = settings
        self.limits = limits or ResourceLimits(
            timeout_seconds=settings.timeout_seconds,
        )
        self.require_validation = require_validation

    def _get_sanitized_env(self) -> Dict[str, str]:
        """Strip privileged secrets from execution environment to prevent exfiltration."""
        return SecretSanitizer.sanitize_environment(
            workspace_root=self.workspace_root,
        )

    def run_command(self, name: str, cmd_str: str) -> ValidationCheck:
        """Run a single validation command under timeout, resource limits, and capture results."""
        start_time = time.time()
        logger.info(f"Running validation [{name}]: {cmd_str}")

        timeout_sec = self.limits.timeout_seconds if self.limits and self.limits.timeout_seconds else self.settings.timeout_seconds
        max_out = self.limits.max_output_bytes


        # POSIX preexec setup for limits and process group isolation
        def _preexec():
            # Create a new process group so we can terminate child trees
            if hasattr(os, "setsid"):
                try:
                    os.setsid()
                except OSError:
                    pass
            # Apply POSIX resource limits
            setup_posix_limits(
                max_memory_bytes=self.limits.max_memory_bytes,
                max_cpu_seconds=self.limits.max_cpu_seconds,
            )

        try:
            proc = subprocess.Popen(
                cmd_str,
                shell=True,
                cwd=str(self.workspace_root),
                env=self._get_sanitized_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=_preexec if os.name == "posix" else None,
            )

            try:
                raw_stdout, raw_stderr = proc.communicate(timeout=timeout_sec)
                timed_out = False
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = -1
                # Terminate entire process group on timeout
                if os.name == "posix" and hasattr(os, "killpg"):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except OSError:
                        pass
                else:
                    proc.kill()
                raw_stdout, raw_stderr = proc.communicate()

            duration = round(time.time() - start_time, 2)
            stdout = truncate_output(raw_stdout or "", max_out)
            stderr = truncate_output(raw_stderr or "", max_out)
            passed = (exit_code == 0) and not timed_out

            # Parse test counts if output is available
            test_stats = parse_test_output(f"{stdout}\n{stderr}")
            tests_run = test_stats.tests_run if test_stats else None
            tests_passed = test_stats.tests_passed if test_stats else None
            tests_failed = test_stats.tests_failed if test_stats else None

            # Classify failure if command did not pass
            failure_cat = None
            if not passed:
                failure_report = FailureClassifier.classify_output(
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timed_out,
                    command_name=name,
                )
                failure_cat = failure_report.category.value

            return ValidationCheck(
                name=name,
                passed=passed,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr if not timed_out else (stderr or f"Command timed out after {timeout_sec}s."),
                duration_seconds=duration,
                command=cmd_str,
                timeout=timed_out,
                failure_category=failure_cat,
                tests_run=tests_run,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
            )

        except Exception as e:
            duration = round(time.time() - start_time, 2)
            logger.error(f"Error executing validation [{name}]: {e}")
            return ValidationCheck(
                name=name,
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_seconds=duration,
                command=cmd_str,
                timeout=False,
                failure_category=FailureCategory.ENVIRONMENT_SETUP_FAILURE.value,
            )

    def validate_task(self, task_id: str, changed_files: Optional[List[str]] = None) -> ValidationResult:
        """Run all configured or discovered validation checks (tests, linting)."""
        checks: List[ValidationCheck] = []
        error_messages = []
        has_tests = False

        lint_command = self.settings.lint_command
        test_command = self.settings.test_command

        # Auto-discovery fallback if commands are not explicitly configured
        if not lint_command or not test_command:
            discovered = TestDiscoverer.discover(self.workspace_root)
            if not lint_command and discovered.lint_command:
                lint_command = discovered.lint_command
            if not test_command:
                test_command = discovered.test_command
                has_tests = discovered.has_tests
            else:
                has_tests = True
        else:
            has_tests = bool(test_command)

        # 1. Run Linting
        if lint_command:
            lint_check = self.run_command("lint", lint_command)
            checks.append(lint_check)
            if not lint_check.passed:
                err_text = lint_check.stderr or lint_check.stdout
                error_messages.append(f"Linting failed (code {lint_check.exit_code}): {err_text}")

        # 2. Run Tests
        if test_command:
            test_check = self.run_command("test", test_command)
            checks.append(test_check)
            if not test_check.passed:
                err_text = test_check.stderr or test_check.stdout
                error_messages.append(f"Tests failed (code {test_check.exit_code}): {err_text}")
        else:
            # Explicitly record that no tests were configured or found
            has_tests = False
            notes = "No test command configured and no automated tests discovered in repository."
            if self.require_validation:
                error_messages.append(f"Validation failed: {notes}")
                checks.append(
                    ValidationCheck(
                        name="test_discovery",
                        passed=False,
                        exit_code=-1,
                        stdout="",
                        stderr=notes,
                        duration_seconds=0.0,
                        failure_category=FailureCategory.ENVIRONMENT_SETUP_FAILURE.value,
                    )
                )
            else:
                checks.append(
                    ValidationCheck(
                        name="test_discovery",
                        passed=True,
                        exit_code=0,
                        stdout=notes,
                        duration_seconds=0.0,
                    )
                )

        # Determine overall passed status
        all_checks_passed = all(c.passed for c in checks)
        overall_passed = all_checks_passed and (has_tests or not self.require_validation)

        # Classify overall failure category
        primary_failure_cat = None
        is_recoverable = False
        if not overall_passed:
            failed_checks = [c for c in checks if not c.passed]
            if failed_checks:
                first_cat = failed_checks[0].failure_category or FailureCategory.UNKNOWN_FAILURE.value
                primary_failure_cat = first_cat
                is_recoverable = FailureClassifier.is_recoverable(FailureCategory(first_cat))
            elif not has_tests:
                primary_failure_cat = FailureCategory.ENVIRONMENT_SETUP_FAILURE.value
                is_recoverable = False

        res = ValidationResult(
            task_id=task_id,
            passed=overall_passed,
            checks=checks,
            error_summary="\n".join(error_messages) if error_messages else None,
            failure_category=primary_failure_cat,
            recoverable=is_recoverable,
            has_tests=has_tests,
            changed_files=changed_files or [],
            diagnostics={
                "has_tests": has_tests,
                "checks_count": len(checks),
                "failed_checks": [c.name for c in checks if not c.passed],
                "recoverable": is_recoverable,
            },
        )

        # Evaluate publishing eligibility
        is_eligible, reason = PublishingEligibility.evaluate(res, require_validation=self.require_validation)
        res.is_publishable = is_eligible
        res.diagnostics["publishing_eligibility_reason"] = reason

        return res
