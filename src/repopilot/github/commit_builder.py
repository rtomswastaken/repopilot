"""Commit creation validation and meaningful commit message formatting."""

import logging
from typing import List, Optional, Tuple

from repopilot.config.models import RepositoryConfig
from repopilot.core.exceptions import (
    SecurityViolationError,
    ValidationFailureError,
)
from repopilot.core.models import ChangePlan, Task, ValidationResult
from repopilot.github.security import SecurityPolicyChecker

logger = logging.getLogger(__name__)


class CommitBuilder:
    """Validates proposed changes against 9 safety requirements prior to commit creation."""

    @classmethod
    def format_commit_message(cls, task: Task, plan: ChangePlan) -> str:
        """Construct a meaningful, structured Git commit message."""
        cat = task.category.value if hasattr(task.category, "value") else str(task.category)
        subject = f"[{cat}] {task.title}"

        changed_paths = [f.path for f in plan.files]
        body_lines = [
            f"Autonomous improvement for task: {task.id}",
            "",
            f"Category: {cat}",
            f"Description: {task.description}",
            "",
            "Modified files:",
        ]
        for p in changed_paths:
            body_lines.append(f"- {p}")

        if task.related_issue_number:
            body_lines.extend(["", f"Fixes #{task.related_issue_number}"])

        body_lines.extend([
            "",
            "Validated autonomously by RepoPilot.",
            "Signed-off-by: RepoPilot <repopilot@users.noreply.github.com>",
        ])

        return f"{subject}\n\n" + "\n".join(body_lines)

    @classmethod
    def verify_pre_commit(
        cls,
        repo_cfg: RepositoryConfig,
        task: Task,
        plan: Optional[ChangePlan],
        starting_revision: str,
        validation: Optional[ValidationResult],
    ) -> Tuple[bool, List[str]]:
        """Enforce all 9 mandatory pre-commit verification requirements:
        1. Verify the target repository.
        2. Verify the task identifier.
        3. Verify the starting revision.
        4. Inspect the complete diff / changes.
        5. Confirm that changed paths are permitted.
        6. Reject unexpected modifications / empty commits.
        7. Check for secrets or sensitive material.
        8. Confirm that mandatory validation succeeded.
        9. Confirm that the publishing policy permits the operation.
        """
        errors: List[str] = []

        # 1. Target repository verification
        if not repo_cfg.owner or not repo_cfg.name:
            errors.append("Invalid or missing target repository configuration.")

        # 2. Task identifier verification
        if not task.id or not task.id.strip():
            errors.append("Task identifier is missing or blank.")

        # 3. Starting revision verification
        if not starting_revision or not starting_revision.strip():
            errors.append("Starting revision (base SHA) is missing or unverified.")

        # 4 & 6. Inspect diff and reject empty commits
        if not plan or not plan.files:
            errors.append("Empty commit rejected: no files or modifications present in plan.")
        else:
            # Check for completely empty content modifications
            has_substantive_change = any(
                (f.content is not None and f.content != "") or (f.diff is not None and f.diff != "")
                for f in plan.files
            )
            if not has_substantive_change:
                errors.append("Empty commit rejected: all files in plan contain empty content.")

        # 5 & 7. Excluded paths and secret scanning
        if plan and plan.files:
            audit = SecurityPolicyChecker.audit_change_plan(plan, repo_cfg.excluded_paths)
            if not audit.is_safe:
                errors.extend(audit.violations)

        # 8. Mandatory validation succeeded
        if not validation:
            errors.append("Missing validation result: commits cannot be created without validation.")
        elif not validation.passed:
            errors.append(
                f"Failed validation blocked publication: {validation.error_summary or 'validation checks failed'}."
            )

        # 9. Publishing policy permits operation
        if task.category not in repo_cfg.allowed_tasks:
            errors.append(
                f"Task category '{task.category.value}' is not permitted by repository configuration."
            )

        is_verified = len(errors) == 0
        return is_verified, errors
