"""Validation policy, publishing eligibility checks, and bounded repair loop controller."""

import logging
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from repopilot.core.models import ValidationResult
from repopilot.validation.classifier import FailureCategory, FailureClassifier, FailureReport

logger = logging.getLogger(__name__)


class PublishingEligibility:
    """Enforces strict criteria that determine whether a validated change is eligible for publishing."""

    @classmethod
    def evaluate(
        cls,
        result: ValidationResult,
        require_validation: bool = True,
    ) -> Tuple[bool, str]:
        """Determine if a ValidationResult is eligible for PR creation / publishing.
        
        Returns (is_eligible, reason_message).
        """
        # Rule 1: Overall passed flag must be True
        if not result.passed:
            return False, f"Validation checks failed: {result.error_summary or 'Unspecified validation failure'}."

        # Rule 2: Cannot publish if no tests were found and validation is mandatory
        if require_validation and not result.has_tests:
            return False, "Rejection: Repository has no automated tests discovered, and validation is mandatory."

        # Rule 3: None of the individual checks may have failed or timed out
        for check in result.checks:
            if not check.passed:
                return False, f"Rejection: Check '{check.name}' did not pass (exit code {check.exit_code})."
            if check.timeout:
                return False, f"Rejection: Check '{check.name}' timed out during execution."

        # Rule 4: If failure category is set to a fatal error, reject
        if result.failure_category and result.failure_category not in (
            "",
            None,
            FailureCategory.UNKNOWN_FAILURE.value,
        ):
            return False, f"Rejection: Classified as failure ({result.failure_category})."

        return True, "Eligible for publishing: All mandatory validation checks passed successfully."


class RepairLoopController:
    """Manages bounded repair attempts, preventing infinite loops and unrecoverable retries."""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries
        self._task_attempts: Dict[str, int] = {}

    def get_attempt_count(self, task_id: str) -> int:
        return self._task_attempts.get(task_id, 0)

    def record_attempt(self, task_id: str) -> int:
        current = self._task_attempts.get(task_id, 0) + 1
        self._task_attempts[task_id] = current
        return current

    def can_attempt_repair(
        self,
        task_id: str,
        failure_report: FailureReport,
        task_max_retries: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """Determine if another repair attempt is allowed under policy.
        
        Returns (can_retry, reason).
        """
        attempt = self.get_attempt_count(task_id)
        effective_max = min(self.max_retries, task_max_retries if task_max_retries is not None else self.max_retries)

        # Check retry budget
        if attempt >= effective_max:
            return False, f"Repair budget exhausted: {attempt}/{effective_max} attempts used."

        # Check recoverability
        if not failure_report.is_recoverable:
            return False, (
                f"Failure category '{failure_report.category.value}' is non-recoverable via code patch "
                f"({failure_report.actionable_hint})."
            )

        return True, f"Eligible for repair attempt {attempt + 1}/{effective_max}."
