"""Merge policy engine enforcing strict, multi-layered automatic merge eligibility."""

import logging
from typing import Dict, List, Optional

from repopilot.config.models import RepositoryConfig
from repopilot.core.models import Task, TaskCategory
from repopilot.github.models import (
    GitHubCheckRun,
    GitHubPullRequest,
    GitHubReview,
    MergeEligibilityResult,
)
from repopilot.github.security import SecurityPolicyChecker

logger = logging.getLogger(__name__)

# Categories inherently safe for auto-merge when CI passes 100%
DEFAULT_AUTO_MERGE_CATEGORIES = {
    TaskCategory.DOCUMENTATION,
    TaskCategory.TESTS,
    TaskCategory.BUG_FIX,
    TaskCategory.CODE_QUALITY,
}


class MergePolicyEngine:
    """Enforces non-negotiable policy checks before any PR can be merged automatically."""

    @classmethod
    def evaluate_merge_eligibility(
        cls,
        repo_cfg: RepositoryConfig,
        task: Task,
        pr: GitHubPullRequest,
        status_checks: Optional[List[GitHubCheckRun]] = None,
        reviews: Optional[List[GitHubReview]] = None,
        repo_permissions: Optional[Dict[str, bool]] = None,
        validated_head_sha: Optional[str] = None,
        current_head_sha: Optional[str] = None,
        require_approval: bool = False,
    ) -> MergeEligibilityResult:
        """Evaluate all 10 mandatory merge eligibility conditions.
        
        Returns MergeEligibilityResult detailing eligibility and missing conditions.
        """
        reasons: List[str] = []
        missing_conditions: List[str] = []
        checks_status = "passed"
        review_status = "approved"

        # Condition 1: Automatic merging enabled in repository configuration
        if not repo_cfg.auto_merge:
            missing_conditions.append("Repository policy has auto_merge=false.")

        # Condition 2: Task category permitted for auto-merge
        if task.category not in DEFAULT_AUTO_MERGE_CATEGORIES:
            missing_conditions.append(
                f"Task category '{task.category.value}' is not eligible for automated merge."
            )

        # Condition 3: Mandatory validation succeeded
        if not task.validation or not task.validation.passed:
            missing_conditions.append("Mandatory validation check did not succeed or is missing.")

        # Condition 4: Required GitHub status checks passed
        if status_checks is not None:
            failed_checks = []
            pending_checks = []
            for check in status_checks:
                if check.status != "completed":
                    pending_checks.append(check.name)
                elif check.conclusion not in ("success", "neutral", "skipped"):
                    failed_checks.append(f"{check.name} ({check.conclusion})")

            if pending_checks:
                checks_status = "pending"
                missing_conditions.append(
                    f"Required status checks pending: {', '.join(pending_checks)}."
                )
            elif failed_checks:
                checks_status = "failed"
                missing_conditions.append(
                    f"Required status checks failed: {', '.join(failed_checks)}."
                )
        else:
            checks_status = "none"

        # Condition 5: Target branch and PR state permit merging
        if pr.draft:
            missing_conditions.append("Draft pull requests cannot be merged.")
        if pr.merged:
            missing_conditions.append("Pull request is already merged.")
        if pr.state != "open":
            missing_conditions.append(f"Pull request is not open (state='{pr.state}').")
        if pr.mergeable_state in ("blocked", "behind"):
            missing_conditions.append(f"PR merge blocked by repository branch protection ({pr.mergeable_state}).")

        # Condition 6: No mandatory review or approval requirement remains unsatisfied
        if reviews is not None:
            has_changes_requested = any(r.state == "CHANGES_REQUESTED" for r in reviews)
            has_approved = any(r.state == "APPROVED" for r in reviews)

            if has_changes_requested:
                review_status = "changes_requested"
                missing_conditions.append("Human reviewer requested changes on pull request.")
            elif require_approval and not has_approved:
                review_status = "pending"
                missing_conditions.append("Mandatory human code review approval required before merge.")
        elif require_approval:
            review_status = "pending"
            missing_conditions.append("Mandatory human code review approval required before merge.")

        # Condition 7: The change does not modify prohibited security-sensitive files
        if task.plan:
            audit = SecurityPolicyChecker.audit_change_plan(task.plan, repo_cfg.excluded_paths)
            if not audit.is_safe:
                missing_conditions.append(
                    f"Security policy violations: {'; '.join(audit.violations)}"
                )
            if audit.sensitive_areas:
                missing_conditions.append(
                    f"Touches security-sensitive areas: {', '.join(audit.sensitive_areas)}."
                )

        # Condition 8: No unresolved conflicts
        if pr.mergeable is False or pr.mergeable_state == "dirty":
            missing_conditions.append(
                f"Pull request contains unresolved merge conflicts (mergeable_state='{pr.mergeable_state}')."
            )

        # Condition 9: Change remains consistent with validated patch revision
        if validated_head_sha and current_head_sha and validated_head_sha != current_head_sha:
            missing_conditions.append(
                f"Head commit changed after validation ({validated_head_sha[:8]} -> {current_head_sha[:8]})."
            )

        # Condition 10: Identity has necessary write/push permissions
        if repo_permissions is not None:
            can_push = repo_permissions.get("push", False) or repo_permissions.get("admin", False)
            if not can_push:
                missing_conditions.append("RepoPilot identity lacks write/push permissions to merge.")

        eligible = len(missing_conditions) == 0

        if eligible:
            reasons.append("All 10 safety and policy conditions satisfied. Eligible for autonomous merge.")
        else:
            reasons.extend(missing_conditions)

        return MergeEligibilityResult(
            eligible=eligible,
            reasons=reasons,
            missing_conditions=missing_conditions,
            checks_status=checks_status,
            review_status=review_status,
        )
