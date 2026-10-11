"""High-level GitHub publishing subsystem coordinating branch, commit, PR, and merge operations."""

import logging
from typing import Optional

from repopilot.config.models import RepositoryConfig
from repopilot.core.exceptions import GitHubAPIError
from repopilot.core.models import ChangePlan, PublishMode, Task, TaskStatus, ValidationResult
from repopilot.github.auth import GitHubCredentials
from repopilot.github.branch_manager import BranchManager
from repopilot.github.client import GitHubClientInterface
from repopilot.github.commit_builder import CommitBuilder
from repopilot.github.conflicts import ConflictDetector
from repopilot.github.merge_engine import MergePolicyEngine
from repopilot.github.models import PublishResult, PublishStatus
from repopilot.github.pr_manager import PullRequestManager

logger = logging.getLogger(__name__)


class GitHubPublisher:
    """Manages the full lifecycle of publishing validated changes to GitHub.
    
    Guarantees:
    - Never publishes unvalidated changes.
    - Never bypasses branch protection or mandatory reviews.
    - Protects existing branches and avoids overwriting unrelated work.
    - Accurately reports validation results in PR descriptions.
    - Enforces 10-condition merge eligibility check before auto-merge.
    """

    def __init__(
        self,
        github_client: GitHubClientInterface,
        credentials: Optional[GitHubCredentials] = None,
    ):
        self.github = github_client
        self.credentials = credentials or GitHubCredentials.from_environment()

    def publish_validated_change(
        self,
        repo_cfg: RepositoryConfig,
        task: Task,
        plan: ChangePlan,
        validation: Optional[ValidationResult],
        starting_base_sha: Optional[str] = None,
        require_approval: bool = False,
    ) -> PublishResult:
        """Publish a validated ChangePlan to the target GitHub repository."""
        full_name = f"{repo_cfg.owner}/{repo_cfg.name}"
        logger.info(f"Initiating publishing pipeline for task [{task.id}] in {full_name}")

        # 1. Fetch current base branch revision
        try:
            repo_info = self.github.get_repo(repo_cfg.owner, repo_cfg.name)
            base_branch_info = self.github.get_branch(repo_cfg.owner, repo_cfg.name, repo_cfg.base_branch)
            current_base_sha = base_branch_info.sha if base_branch_info else "0000000000000000000000000000000000000001"
        except Exception as e:
            msg = f"Failed to inspect target repository {full_name}: {e}"
            logger.error(msg)
            return PublishResult(
                task_id=task.id,
                status=PublishStatus.REJECTED,
                repository=full_name,
                error_message=msg,
            )

        start_sha = starting_base_sha or current_base_sha

        # 2. Strict Pre-Commit Verification (9 Requirements)
        is_verified, verification_errors = CommitBuilder.verify_pre_commit(
            repo_cfg=repo_cfg,
            task=task,
            plan=plan,
            starting_revision=start_sha,
            validation=validation,
        )
        if not is_verified:
            err_msg = "; ".join(verification_errors)
            logger.warning(f"Publishing rejected for task [{task.id}]: {err_msg}")
            return PublishResult(
                task_id=task.id,
                status=PublishStatus.REJECTED,
                repository=full_name,
                error_message=err_msg,
            )

        # 3. Conflict / Base Drift Detection
        if starting_base_sha and starting_base_sha != current_base_sha:
            changed_files = [f.path for f in plan.files]
            conflict = ConflictDetector.analyze_base_drift(
                repository=full_name,
                branch=repo_cfg.base_branch,
                starting_base_sha=starting_base_sha,
                current_base_sha=current_base_sha,
                changed_files=changed_files,
            )
            if conflict and not conflict.recoverable:
                return PublishResult(
                    task_id=task.id,
                    status=PublishStatus.CONFLICT,
                    repository=full_name,
                    conflict_result=conflict,
                    error_message=conflict.reason,
                )

        # 4. Safe Branch Management
        cat_val = task.category.value if hasattr(task.category, "value") else str(task.category)
        branch_name = BranchManager.generate_branch_name(
            task_id=task.id,
            title=task.title,
            category=cat_val,
        )

        try:
            existing_branch = self.github.get_branch(repo_cfg.owner, repo_cfg.name, branch_name)
            if existing_branch:
                # Verify branch ownership
                if not BranchManager.is_repopilot_owned_branch(branch_name, task.id):
                    err = f"Branch '{branch_name}' already exists and is not owned by RepoPilot. Halting to avoid overwriting unrelated work."
                    logger.error(err)
                    return PublishResult(
                        task_id=task.id,
                        status=PublishStatus.BLOCKED,
                        repository=full_name,
                        branch_name=branch_name,
                        error_message=err,
                    )
                logger.info(f"Reusing existing RepoPilot-owned task branch: {branch_name}")
            else:
                self.github.create_branch(repo_cfg.owner, repo_cfg.name, repo_cfg.base_branch, branch_name)
                logger.info(f"Created new branch '{branch_name}' from '{repo_cfg.base_branch}'")
        except Exception as e:
            err = f"Branch creation failed for '{branch_name}': {e}"
            logger.error(err)
            return PublishResult(
                task_id=task.id,
                status=PublishStatus.REJECTED,
                repository=full_name,
                branch_name=branch_name,
                error_message=err,
            )

        # 5. Commit Creation
        commit_message = CommitBuilder.format_commit_message(task, plan)
        try:
            # Build git tree entries
            tree_entries = []
            for file_change in plan.files:
                blob_sha = self.github.create_blob(
                    repo_cfg.owner,
                    repo_cfg.name,
                    file_change.content or "",
                )
                tree_entries.append({
                    "path": file_change.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                })

            base_tree_sha = current_base_sha
            tree_sha = self.github.create_tree(repo_cfg.owner, repo_cfg.name, base_tree_sha, tree_entries)
            commit = self.github.create_commit(
                owner=repo_cfg.owner,
                name=repo_cfg.name,
                message=commit_message,
                tree_sha=tree_sha,
                parents=[current_base_sha],
            )
            # Update branch ref to point to the new commit
            self.github.update_ref(repo_cfg.owner, repo_cfg.name, f"heads/{branch_name}", commit.sha)
            commit_sha = commit.sha
            logger.info(f"Created commit {commit_sha[:8]} on branch {branch_name}")
        except Exception as e:
            err = f"Commit creation failed on branch '{branch_name}': {e}"
            logger.error(err)
            return PublishResult(
                task_id=task.id,
                status=PublishStatus.REJECTED,
                repository=full_name,
                branch_name=branch_name,
                error_message=err,
            )

        # 6. Pull Request Management (Check duplicates & updates)
        try:
            open_prs = self.github.list_pull_requests(repo_cfg.owner, repo_cfg.name, state="open")
            existing_pr = PullRequestManager.find_existing_task_pr(open_prs, task.id, branch_name)

            pr_title = PullRequestManager.format_pr_title(task)
            pr_body = PullRequestManager.format_pr_body(task, plan, validation)
            is_draft = (repo_cfg.publish_mode == PublishMode.DRAFT_PR)

            if existing_pr:
                if not PullRequestManager.is_repopilot_owned_pr(existing_pr, task.id):
                    err = f"Existing pull request #{existing_pr.number} is not owned by RepoPilot. Cannot modify."
                    logger.error(err)
                    return PublishResult(
                        task_id=task.id,
                        status=PublishStatus.BLOCKED,
                        repository=full_name,
                        branch_name=branch_name,
                        commit_sha=commit_sha,
                        pr_number=existing_pr.number,
                        pr_url=existing_pr.html_url,
                        error_message=err,
                    )
                # Update existing PR
                pr = self.github.update_pull_request(
                    repo_cfg.owner,
                    repo_cfg.name,
                    existing_pr.number,
                    title=pr_title,
                    body=pr_body,
                )
                publish_status = PublishStatus.UPDATED
                logger.info(f"Updated existing RepoPilot PR #{pr.number}: {pr.html_url}")
            else:
                pr = self.github.create_pull_request(
                    owner=repo_cfg.owner,
                    name=repo_cfg.name,
                    title=pr_title,
                    body=pr_body,
                    head_branch=branch_name,
                    base_branch=repo_cfg.base_branch,
                    draft=is_draft,
                )
                self.github.add_labels(repo_cfg.owner, repo_cfg.name, pr.number, ["repopilot", "automated"])
                publish_status = PublishStatus.PUBLISHED
                logger.info(f"Created new PR #{pr.number}: {pr.html_url}")

            # Check PR for Git merge conflicts
            pr_conflict = ConflictDetector.analyze_pr_merge_conflict(
                repository=full_name,
                pr=pr,
                base_sha=current_base_sha,
                head_sha=commit_sha,
            )
            if pr_conflict:
                return PublishResult(
                    task_id=task.id,
                    status=PublishStatus.CONFLICT,
                    repository=full_name,
                    branch_name=branch_name,
                    commit_sha=commit_sha,
                    pr_number=pr.number,
                    pr_url=pr.html_url,
                    conflict_result=pr_conflict,
                    error_message=pr_conflict.reason,
                )

        except Exception as e:
            err = f"Pull request creation or update failed: {e}"
            logger.error(err)
            return PublishResult(
                task_id=task.id,
                status=PublishStatus.REJECTED,
                repository=full_name,
                branch_name=branch_name,
                commit_sha=commit_sha,
                error_message=err,
            )

        # 7. Explicit Merge Eligibility Evaluation & Policy Check
        merged = False
        merge_result = None

        if repo_cfg.auto_merge:
            logger.info(f"Evaluating auto-merge eligibility for PR #{pr.number} in {full_name}...")
            status_checks = self.github.list_check_runs(repo_cfg.owner, repo_cfg.name, commit_sha)
            reviews = self.github.list_reviews(repo_cfg.owner, repo_cfg.name, pr.number)

            merge_result = MergePolicyEngine.evaluate_merge_eligibility(
                repo_cfg=repo_cfg,
                task=task,
                pr=pr,
                status_checks=status_checks,
                reviews=reviews,
                repo_permissions=repo_info.permissions,
                validated_head_sha=commit_sha,
                current_head_sha=commit_sha,
                require_approval=require_approval,
            )

            if merge_result.eligible:
                try:
                    merged = self.github.merge_pull_request(
                        owner=repo_cfg.owner,
                        name=repo_cfg.name,
                        pr_number=pr.number,
                        commit_title=f"Merge autonomous PR #{pr.number}: {pr_title}",
                    )
                    logger.info(f"Auto-merged PR #{pr.number} successfully.")
                except Exception as e:
                    logger.warning(f"Merge execution failed for PR #{pr.number}: {e}")
                    merged = False
            else:
                logger.info(
                    f"PR #{pr.number} is NOT eligible for auto-merge. Leaving open. Reasons: {'; '.join(merge_result.missing_conditions)}"
                )

        # Update task model state
        task.branch_name = branch_name
        task.pr_number = pr.number
        task.pr_url = pr.html_url
        task.status = TaskStatus.PUBLISHED

        return PublishResult(
            task_id=task.id,
            status=publish_status,
            repository=full_name,
            branch_name=branch_name,
            commit_sha=commit_sha,
            pr_number=pr.number,
            pr_url=pr.html_url,
            merged=merged,
            merge_result=merge_result,
        )
