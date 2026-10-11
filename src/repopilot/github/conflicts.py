"""Structured conflict detection and recovery assessment for Git operations."""

import logging
from typing import Any, Dict, List, Optional

from repopilot.github.models import ConflictResult, GitHubPullRequest

logger = logging.getLogger(__name__)


class ConflictDetector:
    """Detects and analyzes branch drift and pull request merge conflicts."""

    @classmethod
    def analyze_base_drift(
        cls,
        repository: str,
        branch: str,
        starting_base_sha: str,
        current_base_sha: str,
        changed_files: List[str],
        base_modified_files: Optional[List[str]] = None,
    ) -> Optional[ConflictResult]:
        """Assess whether a base branch update creates a recoverable or unrecoverable conflict."""
        if starting_base_sha == current_base_sha:
            return None

        # Check for overlapping modified files between our changes and remote updates
        overlapping: List[str] = []
        if base_modified_files:
            overlapping = [f for f in changed_files if f in base_modified_files]

        recoverable = len(overlapping) == 0

        reason = (
            f"Base branch revision advanced from {starting_base_sha[:8]} to {current_base_sha[:8]}."
        )
        if overlapping:
            reason += f" Unrecoverable file overlap on: {', '.join(overlapping)}."
        else:
            reason += " Non-overlapping changes; recoverable via re-validation."

        logger.warning(f"Conflict detected in {repository} ({branch}): {reason}")

        return ConflictResult(
            repository=repository,
            branch=branch,
            base_revision=starting_base_sha,
            current_revision=current_base_sha,
            recoverable=recoverable,
            reason=reason,
            details={
                "changed_files": changed_files,
                "base_modified_files": base_modified_files or [],
                "overlapping_files": overlapping,
            },
        )

    @classmethod
    def analyze_pr_merge_conflict(
        cls,
        repository: str,
        pr: GitHubPullRequest,
        base_sha: str,
        head_sha: str,
    ) -> Optional[ConflictResult]:
        """Detect Git merge conflict from GitHub PR mergeable status."""
        if pr.mergeable is False or pr.mergeable_state == "dirty":
            reason = f"Pull Request #{pr.number} has Git merge conflicts (mergeable_state='{pr.mergeable_state}')."
            logger.warning(f"PR conflict in {repository} #{pr.number}: {reason}")
            return ConflictResult(
                repository=repository,
                branch=pr.head_branch,
                base_revision=base_sha,
                current_revision=head_sha,
                recoverable=False,
                reason=reason,
                details={
                    "pr_number": pr.number,
                    "mergeable": pr.mergeable,
                    "mergeable_state": pr.mergeable_state,
                },
            )
        return None
