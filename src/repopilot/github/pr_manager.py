"""Pull request formatting, lifecycle management, and duplicate deduplication."""

import logging
import re
from typing import List, Optional

from repopilot.core.models import ChangePlan, Task, ValidationResult
from repopilot.github.models import GitHubPullRequest

logger = logging.getLogger(__name__)

REPOPILOT_SIGNATURE_TEMPLATE = "<!-- repopilot:task_id={task_id} -->"


class PullRequestManager:
    """Handles structured PR body generation, existing PR detection, and ownership validation."""

    @classmethod
    def format_pr_title(cls, task: Task) -> str:
        """Create a clear, concise pull request title."""
        cat = task.category.value if hasattr(task.category, "value") else str(task.category)
        return f"[{cat}] {task.title}"

    @classmethod
    def format_pr_body(
        cls,
        task: Task,
        plan: ChangePlan,
        validation: Optional[ValidationResult] = None,
    ) -> str:
        """Format a detailed and accurate PR description.
        
        Strictly reflects actual validation outcomes without exaggeration.
        """
        lines = [
            f"## ✈️ Autonomous Improvement: {task.title}",
            "",
            "### 📋 Task Summary",
            f"- **Task ID**: `{task.id}`",
            f"- **Category**: `{task.category.value}`",
            f"- **Description**: {task.description}",
            "",
            "### 🎯 Rationale & Impact",
            f"- **Risk Level**: `{plan.risk_level}`",
            f"- **Estimated Impact**: {plan.estimated_impact or 'Targeted localized improvement.'}",
            "",
            "### 📝 Modified Files",
            "| File | Action | Rationale |",
            "| :--- | :---: | :--- |",
        ]

        for file_change in plan.files:
            act = file_change.action.value if hasattr(file_change.action, "value") else str(file_change.action)
            rat = file_change.rationale or "Autonomous improvement"
            lines.append(f"| `{file_change.path}` | `{act}` | {rat} |")

        lines.extend(["", "### 🧪 Validation Results"])

        if validation and validation.checks:
            lines.extend([
                "| Check | Status | Exit Code | Duration |",
                "| :--- | :---: | :---: | :---: |",
            ])
            for check in validation.checks:
                status_icon = "✅ PASS" if check.passed else "❌ FAIL"
                lines.append(
                    f"| `{check.name}` | {status_icon} | {check.exit_code} | {check.duration_seconds:.2f}s |"
                )
            if validation.passed:
                lines.append("\n> **Verification Status**: ✅ All automated tests and linters passed.")
            else:
                lines.append(
                    f"\n> **Verification Status**: ❌ Automated checks failed: {validation.error_summary or 'Unknown error'}"
                )
        elif validation and validation.passed:
            lines.append("> **Verification Status**: ✅ Pre-commit validation passed.")
        else:
            lines.append("> **Verification Status**: ⚠️ No automated check results recorded.")

        lines.extend([
            "",
            "### ⚠️ Known Limitations & Review Notes",
            "- Changes were generated autonomously by RepoPilot.",
            "- Please review before merging to ensure compliance with codebase conventions.",
        ])

        if task.related_issue_number:
            lines.extend([
                "",
                "### 🔗 Related Issues",
                f"- Fixes #{task.related_issue_number}",
            ])

        # RepoPilot metadata signature for ownership verification
        lines.extend([
            "",
            "---",
            REPOPILOT_SIGNATURE_TEMPLATE.format(task_id=task.id),
            "*Generated and published autonomously by [RepoPilot](https://github.com/repopilot).*",
        ])

        return "\n".join(lines)

    @classmethod
    def is_repopilot_owned_pr(cls, pr: GitHubPullRequest, task_id: Optional[str] = None) -> bool:
        """Determine whether RepoPilot owns the given pull request."""
        # 1. Check branch prefix
        if pr.head_branch.startswith("repopilot/"):
            if task_id:
                clean_id = re.sub(r"[^a-zA-Z0-9_\-]+", "", task_id)
                return clean_id in pr.head_branch or (pr.body and f"repopilot:task_id={task_id}" in pr.body)
            return True

        # 2. Check metadata signature in body
        if pr.body:
            if "<!-- repopilot:" in pr.body:
                if task_id:
                    return f"repopilot:task_id={task_id}" in pr.body
                return True

        return False

    @classmethod
    def find_existing_task_pr(
        cls,
        pull_requests: List[GitHubPullRequest],
        task_id: str,
        head_branch: str,
    ) -> Optional[GitHubPullRequest]:
        """Detect an existing pull request corresponding to the task or branch."""
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]+", "", task_id)

        for pr in pull_requests:
            # Match by exact branch name
            if pr.head_branch == head_branch:
                return pr
            # Match by task_id signature in body
            if pr.body and f"repopilot:task_id={task_id}" in pr.body:
                return pr
            # Match by task ID embedded in branch name
            if pr.head_branch.startswith("repopilot/") and clean_id in pr.head_branch:
                return pr

        return None
