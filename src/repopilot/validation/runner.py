"""Automated validation runner for tests and linting in isolated workspaces."""

import logging
import os
from pathlib import Path
import subprocess
import time
from typing import Dict, List, Optional

from repopilot.config.models import ValidationSettings
from repopilot.core.models import ValidationCheck, ValidationResult

import sys

logger = logging.getLogger(__name__)

# Environment variables to strip before executing untrusted repository code
PRIVILEGED_ENV_VARS = [
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_ACTION",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
]


class ValidationRunner:
    """Executes tests and linters in the isolated workspace with security boundaries."""

    def __init__(self, workspace_root: Path, settings: ValidationSettings):
        self.workspace_root = workspace_root.resolve()
        self.settings = settings

    def _get_sanitized_env(self) -> Dict[str, str]:
        """Strip privileged secrets from execution environment to prevent exfiltration."""
        env = dict(os.environ)
        for secret_key in PRIVILEGED_ENV_VARS:
            env.pop(secret_key, None)
        # Ensure active virtualenv bin is available in PATH
        bin_dir = str(Path(sys.executable).parent)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        # Ensure workspace is at the head of PYTHONPATH if relevant
        env["PYTHONPATH"] = str(self.workspace_root) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def run_command(self, name: str, cmd_str: str) -> ValidationCheck:
        """Run a single validation command under timeout and capture results."""
        start_time = time.time()
        logger.info(f"Running validation [{name}]: {cmd_str}")

        try:
            # Run using shell=True to support pipe / flags specified in config, within isolated workspace
            proc = subprocess.run(
                cmd_str,
                shell=True,
                cwd=str(self.workspace_root),
                env=self._get_sanitized_env(),
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
            duration = time.time() - start_time
            passed = proc.returncode == 0

            return ValidationCheck(
                name=name,
                passed=passed,
                exit_code=proc.returncode,
                stdout=proc.stdout[-5000:],  # Bounded output capture
                stderr=proc.stderr[-5000:],
                duration_seconds=round(duration, 2),
            )
        except subprocess.TimeoutExpired as e:
            duration = time.time() - start_time
            logger.warning(f"Validation command [{name}] timed out after {self.settings.timeout_seconds}s.")
            return ValidationCheck(
                name=name,
                passed=False,
                exit_code=-1,
                stdout=e.stdout.decode() if isinstance(e.stdout, bytes) else str(e.stdout or ""),
                stderr=f"Command timed out after {self.settings.timeout_seconds} seconds.",
                duration_seconds=round(duration, 2),
            )
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Error executing validation [{name}]: {e}")
            return ValidationCheck(
                name=name,
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_seconds=round(duration, 2),
            )

    def validate_task(self, task_id: str) -> ValidationResult:
        """Run all configured validation checks (tests, linting)."""
        checks: List[ValidationCheck] = []
        all_passed = True
        error_messages = []

        if self.settings.lint_command:
            lint_check = self.run_command("lint", self.settings.lint_command)
            checks.append(lint_check)
            if not lint_check.passed:
                all_passed = False
                error_messages.append(f"Linting failed (code {lint_check.exit_code}): {lint_check.stderr or lint_check.stdout}")

        if self.settings.test_command:
            test_check = self.run_command("test", self.settings.test_command)
            checks.append(test_check)
            if not test_check.passed:
                all_passed = False
                error_messages.append(f"Tests failed (code {test_check.exit_code}): {test_check.stderr or test_check.stdout}")

        # If no commands are configured, we treat as passed with an advisory notice
        if not checks:
            checks.append(
                ValidationCheck(
                    name="default_check",
                    passed=True,
                    exit_code=0,
                    stdout="No explicit test or lint command configured.",
                    duration_seconds=0.0,
                )
            )

        return ValidationResult(
            task_id=task_id,
            passed=all_passed,
            checks=checks,
            error_summary="\n".join(error_messages) if error_messages else None,
        )
