"""Failure classification and actionable diagnostics for validation and execution."""

from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from repopilot.core.exceptions import (
    ExecutionTimeoutError,
    PatchApplicationError,
    ProhibitedPathError,
    ResourceLimitExceededError,
    SecurityViolationError,
    SymlinkEscapeError,
)

logger = logging.getLogger(__name__)


class FailureCategory(str, Enum):
    """Normalized categories for execution and validation failures."""
    PATCH_APPLICATION_FAILURE = "patch_application_failure"
    MISSING_DEPENDENCIES = "missing_dependencies"
    TEST_FAILURE = "test_failure"
    LINT_FAILURE = "lint_failure"
    SYNTAX_ERROR = "syntax_error"
    EXECUTION_TIMEOUT = "execution_timeout"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    PROHIBITED_FILE_CHANGE = "prohibited_file_change"
    ENVIRONMENT_SETUP_FAILURE = "environment_setup_failure"
    UNKNOWN_FAILURE = "unknown_failure"


class FailureReport(BaseModel):
    """Detailed actionable diagnostic report of a validation failure."""
    category: FailureCategory
    is_recoverable: bool
    summary: str
    details: str = ""
    exit_code: int = -1
    extracted_errors: List[str] = Field(default_factory=list)
    actionable_hint: str = ""
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


class FailureClassifier:
    """Classifies command outputs and exceptions into structured failure categories."""

    # Regex patterns for matching common error signatures
    SYNTAX_PATTERNS = [
        r"SyntaxError:.*",
        r"IndentationError:.*",
        r"TabError:.*",
        r"ParseError:.*",
        r"Syntax error:.*",
        r"unexpected token.*",
        r"Cannot find name.*",
        r"TS\d{4}:.*",  # TypeScript compiler errors
    ]

    MISSING_DEP_PATTERNS = [
        r"ModuleNotFoundError: No module named ['\"]?([^'\"]+)['\"]?",
        r"ImportError: cannot import name.*",
        r"Cannot find module ['\"]?([^'\"]+)['\"]?",
        r"npm ERR! code ENOENT.*",
        r"yarn error.*Couldn't find.*",
        r"pip: command not found",
        r"pkg_resources\.DistributionNotFound.*",
    ]

    LINT_PATTERNS = [
        r".+:\d+:\d+: [EFW]\d+ .*",  # Flake8 / Ruff errors
        r"ruff: error.*",
        r"eslint: error.*",
        r"prettier --check.*failed",
        r"mypy: error.*",
        r"error: unused import.*",
    ]

    TEST_FAILURE_PATTERNS = [
        r"FAILED .+::.+",
        r"FAILURES",
        r"AssertionError:?.*",
        r"Tests: +\d+ failed",
        r"FAIL .+",
        r"FAILURES! Tests run:.*Failures:.*",
        r"expected .+ to (equal|be|match) .+",
    ]

    ENV_SETUP_PATTERNS = [
        r"command not found",
        r"No such file or directory",
        r"/bin/sh: .+: not found",
        r"Permission denied",
        r"EACCES",
    ]

    RESOURCE_PATTERNS = [
        r"MemoryError",
        r"out of memory",
        r"Killed",
        r"fatal error: runtime: out of memory",
        r"JavaScript heap out of memory",
        r"ResourceLimitExceededError",
    ]

    @classmethod
    def is_recoverable(cls, category: FailureCategory) -> bool:
        """Determine if a failure is potentially recoverable through AI code modification."""
        # Code defects that an LLM can fix by modifying repository code:
        # Syntax errors, test assertion failures, linting violations, and minor patch issues.
        # Environment, missing system packages, security policy violations, and OOM/timeouts
        # are environmental or policy boundaries and cannot be safely fixed by AI code edits alone.
        return category in {
            FailureCategory.TEST_FAILURE,
            FailureCategory.LINT_FAILURE,
            FailureCategory.SYNTAX_ERROR,
            FailureCategory.PATCH_APPLICATION_FAILURE,
        }

    @classmethod
    def classify_exception(cls, exc: Exception) -> FailureReport:
        """Classify an exception raised during workspace prep, patch, or execution."""
        msg = str(exc)

        if isinstance(exc, (ProhibitedPathError, SymlinkEscapeError)):
            return FailureReport(
                category=FailureCategory.PROHIBITED_FILE_CHANGE,
                is_recoverable=False,
                summary=f"Security violation: {msg}",
                actionable_hint="The proposed patch touched forbidden or sensitive paths. Rejected by policy.",
            )
        if isinstance(exc, SecurityViolationError):
            return FailureReport(
                category=FailureCategory.PROHIBITED_FILE_CHANGE,
                is_recoverable=False,
                summary=f"Security violation: {msg}",
                actionable_hint="Security policy prevented execution.",
            )
        if isinstance(exc, ExecutionTimeoutError):
            return FailureReport(
                category=FailureCategory.EXECUTION_TIMEOUT,
                is_recoverable=False,
                summary="Execution timed out.",
                actionable_hint="Validation command exceeded the timeout budget.",
            )
        if isinstance(exc, ResourceLimitExceededError):
            return FailureReport(
                category=FailureCategory.RESOURCE_LIMIT_EXCEEDED,
                is_recoverable=False,
                summary=f"Resource limit exceeded: {msg}",
                actionable_hint="Workspace disk, patch size, or memory limit was exceeded.",
            )
        if isinstance(exc, PatchApplicationError):
            return FailureReport(
                category=FailureCategory.PATCH_APPLICATION_FAILURE,
                is_recoverable=True,
                summary=f"Patch application failed: {msg}",
                actionable_hint="The generated diff could not be applied cleanly or conflicted.",
            )

        return FailureReport(
            category=FailureCategory.UNKNOWN_FAILURE,
            is_recoverable=False,
            summary=f"Unexpected error: {msg}",
            actionable_hint="Investigate the unhandled exception.",
        )

    @classmethod
    def classify_output(
        cls,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
        timeout: bool = False,
        command_name: str = "",
    ) -> FailureReport:
        """Classify command execution results based on exit codes, stdout, and stderr."""
        combined = f"{stdout}\n{stderr}"

        if timeout:
            return FailureReport(
                category=FailureCategory.EXECUTION_TIMEOUT,
                is_recoverable=False,
                summary="Command timed out.",
                exit_code=exit_code,
                actionable_hint="Validation process timed out and was terminated.",
            )

        if exit_code == 0:
            return FailureReport(
                category=FailureCategory.UNKNOWN_FAILURE,
                is_recoverable=True,
                summary="Command succeeded.",
                exit_code=0,
            )

        # 1. Environment / command missing
        if exit_code == 127 or any(re.search(p, combined, re.IGNORECASE) for p in cls.ENV_SETUP_PATTERNS):
            return FailureReport(
                category=FailureCategory.ENVIRONMENT_SETUP_FAILURE,
                is_recoverable=False,
                summary=f"Command or runtime not found in environment (exit code {exit_code}).",
                exit_code=exit_code,
                actionable_hint="Required test runner or runtime is missing from PATH.",
            )

        # 2. Resource limit exceeded / OOM
        if exit_code == 137 or any(re.search(p, combined, re.IGNORECASE) for p in cls.RESOURCE_PATTERNS):
            return FailureReport(
                category=FailureCategory.RESOURCE_LIMIT_EXCEEDED,
                is_recoverable=False,
                summary="Execution terminated due to memory or resource limits.",
                exit_code=exit_code,
                actionable_hint="Command exceeded maximum allowable memory or was killed by SIGKILL (137).",
            )

        # 3. Missing dependencies
        dep_matches = []
        for p in cls.MISSING_DEP_PATTERNS:
            found = re.findall(p, combined, re.IGNORECASE)
            if found:
                dep_matches.extend(found if isinstance(found[0], str) else [m[0] for m in found])
        if dep_matches:
            unique_deps = sorted(list(set(dep_matches)))
            return FailureReport(
                category=FailureCategory.MISSING_DEPENDENCIES,
                is_recoverable=False,
                summary=f"Missing dependencies: {', '.join(unique_deps[:3])}",
                exit_code=exit_code,
                extracted_errors=unique_deps,
                actionable_hint="Project requires uninstalled packages. Ensure dependencies are installed in sandbox.",
            )


        # 4. Syntax error
        syntax_errors = []
        for p in cls.SYNTAX_PATTERNS:
            found = re.findall(p, combined)
            if found:
                syntax_errors.extend(found)
        if syntax_errors:
            return FailureReport(
                category=FailureCategory.SYNTAX_ERROR,
                is_recoverable=True,
                summary=f"Syntax or parsing error: {syntax_errors[0]}",
                exit_code=exit_code,
                extracted_errors=syntax_errors[:5],
                actionable_hint="Correct the syntax or compiler errors in the modified files.",
            )

        # 5. Lint error
        if command_name.lower() == "lint" or any(re.search(p, combined) for p in cls.LINT_PATTERNS):
            return FailureReport(
                category=FailureCategory.LINT_FAILURE,
                is_recoverable=True,
                summary="Linter violations detected.",
                exit_code=exit_code,
                actionable_hint="Fix style and lint issues identified by the linter.",
            )

        # 6. Test failure
        test_errors = []
        for p in cls.TEST_FAILURE_PATTERNS:
            found = re.findall(p, combined)
            if found:
                test_errors.extend(found if isinstance(found[0], str) else [m[0] for m in found])
        if test_errors or command_name.lower() == "test":
            return FailureReport(
                category=FailureCategory.TEST_FAILURE,
                is_recoverable=True,
                summary="Automated tests failed.",
                exit_code=exit_code,
                extracted_errors=test_errors[:5],
                actionable_hint="Analyze failed assertions and revise code to satisfy tests.",
            )

        # Default fallback
        return FailureReport(
            category=FailureCategory.UNKNOWN_FAILURE,
            is_recoverable=True,
            summary=f"Validation failed with exit code {exit_code}.",
            exit_code=exit_code,
            actionable_hint="Inspect standard error output to diagnose the issue.",
        )
