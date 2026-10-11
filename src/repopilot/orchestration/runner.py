"""Orchestration engine executing the autonomous multi-repository lifecycle."""

from datetime import datetime
import logging
from pathlib import Path
import time
import uuid
from typing import Dict, List, Optional

from repopilot.ai.provider import AICodeChangeRequest, AIPlanRequest, AIProviderInterface
from repopilot.ai.repair_planner import RepairPlanner
from repopilot.config.models import RepoPilotConfig, RepositoryConfig
from repopilot.core.exceptions import RepoPilotError, SecurityViolationError
from repopilot.core.models import (
    BudgetTracker,
    ExecutionReport,
    PublishMode,
    RepoExecutionSummary,
    Task,
    TaskCategory,
    TaskExecutionSummary,
    TaskStatus,
)
from repopilot.execution.patch import PatchApplier
from repopilot.execution.workspace import IsolatedWorkspace
from repopilot.github.client import GitHubClientInterface
from repopilot.github.models import PublishStatus
from repopilot.github.publisher import GitHubPublisher
from repopilot.intelligence.analyzer import RepositoryAnalyzerInterface
from repopilot.reporting.reporter import ExecutionReporter
from repopilot.state.models import CheckpointStage
from repopilot.state.store import StateStoreInterface
from repopilot.validation.runner import ValidationRunner

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates repository discovery, analysis, planning, validation, and publishing."""

    def __init__(
        self,
        config: RepoPilotConfig,
        github_client: GitHubClientInterface,
        ai_provider: AIProviderInterface,
        state_store: StateStoreInterface,
        analyzer: RepositoryAnalyzerInterface,
        workspace_base: Optional[Path] = None,
        repo_source_base: Optional[Path] = None,
    ):
        self.config = config
        self.github = github_client
        self.ai = ai_provider
        self.state_store = state_store
        self.analyzer = analyzer
        self.publisher = GitHubPublisher(github_client=self.github)
        self.repair_planner = RepairPlanner(max_retries=config.settings.max_retries_per_task)
        self.workspace_base = workspace_base or Path(".repopilot/workspaces")
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.repo_source_base = Path(repo_source_base).resolve() if repo_source_base else None

        self.budget = BudgetTracker(
            max_execution_minutes=config.settings.max_execution_minutes,
            max_ai_requests=config.settings.max_ai_requests_per_run,
            max_tasks_per_repo=config.settings.max_tasks_per_repository,
            max_repositories=config.settings.max_repositories_per_run,
        )

    def run(self) -> ExecutionReport:
        """Execute the full 17-step autonomous lifecycle."""
        run_id = f"run-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        logger.info(f"Starting autonomous run: {run_id}")

        report = ExecutionReport(
            run_id=run_id,
            started_at=datetime.utcnow(),
            total_repositories=len(self.config.repositories),
        )

        # Step 4: Discover enabled repositories
        enabled_repos = [r for r in self.config.repositories if r.enabled][:self.config.settings.max_repositories_per_run]

        for repo_cfg in enabled_repos:
            # Check execution budget limits
            if self.budget.is_time_exhausted():
                msg = f"Time budget exhausted ({self.config.settings.max_execution_minutes}m). Stopping run."
                logger.warning(msg)
                report.errors.append(msg)
                break

            repo_summary = self._process_repository(repo_cfg)
            report.repositories.append(repo_summary)
            report.processed_repositories += 1

        report.completed_at = datetime.utcnow()
        report.total_ai_requests = self.budget.ai_requests_used
        report.total_changes_published = sum(r.success_count for r in report.repositories)
        report.state_saved = True

        # Step 14 & 15: Generate and export summary
        ExecutionReporter.write_github_step_summary(report)
        logger.info(f"Completed run {run_id}. Processed {report.processed_repositories} repositories.")

        return report

    def _process_repository(self, repo_cfg: RepositoryConfig) -> RepoExecutionSummary:
        """Process a single repository with strict failure isolation."""
        full_name = f"{repo_cfg.owner}/{repo_cfg.name}"
        logger.info(f"Processing repository: {full_name}")
        start_time = time.time()

        summary = RepoExecutionSummary(repository=full_name)

        try:
            # Fetch remote issues
            remote_issues = self.github.list_open_issues(repo_cfg.owner, repo_cfg.name)

            # Isolated workspace for analysis and development
            with IsolatedWorkspace(base_dir=self.workspace_base) as ws:
                workspace_dir = ws.path
                if not workspace_dir:
                    raise RuntimeError("Could not enter workspace")

                # If local repository source is available, populate workspace
                source_dir = None
                if self.repo_source_base:
                    cand = self.repo_source_base / repo_cfg.name
                    if cand.exists() and cand.is_dir():
                        source_dir = cand

                if not source_dir:
                    for cand in [Path(repo_cfg.name), Path(f"{repo_cfg.owner}/{repo_cfg.name}")]:
                        if cand.exists() and cand.is_dir() and cand.resolve() != workspace_dir.resolve():
                            source_dir = cand
                            break

                if source_dir:
                    ws.populate_from(source_dir)

                # Fetch remote base branch SHA to align revisions
                remote_base_sha = None
                try:
                    base_info = self.github.get_branch(repo_cfg.owner, repo_cfg.name, repo_cfg.base_branch)
                    if base_info:
                        remote_base_sha = base_info.sha
                except Exception:
                    pass

                starting_revision = ws.record_starting_revision(remote_base_sha)

                # Step 5: Inspect repository context
                repo_context = self.analyzer.analyze(workspace_dir, issues=remote_issues)

                # Retrieve existing task history to prevent redundant planning
                current_state = self.state_store.load_state()
                task_history_titles = []
                if full_name in current_state.repositories:
                    task_history_titles = [
                        t.title for t in current_state.repositories[full_name].task_history.values()
                    ]

                # Step 6: Identify candidate improvements
                plan_request = AIPlanRequest(
                    repo_owner=repo_cfg.owner,
                    repo_name=repo_cfg.name,
                    goals=repo_cfg.goals,
                    allowed_tasks=[cat.value for cat in repo_cfg.allowed_tasks],
                    file_tree=repo_context.file_tree,
                    open_issues=repo_context.open_issues,
                    tech_stack={
                        "languages": repo_context.languages,
                        "frameworks": repo_context.frameworks,
                        "test_frameworks": repo_context.test_frameworks,
                        "ast_summary": getattr(repo_context, "ast_summary", {}),
                    },
                    repo_context=repo_context,
                    readme_content=repo_context.readme_content,
                    doc_files=repo_context.doc_files,
                    task_history=task_history_titles,
                )

                if self.budget.is_ai_budget_exhausted() or not self.ai.is_available():
                    logger.info("AI provider unavailable or budget exhausted. Skipping AI-driven planning.")
                    candidate_tasks = []
                else:
                    self.budget.record_ai_request()
                    candidate_tasks = self.ai.plan_improvements(plan_request)

                # Step 7: Check task history for duplicates
                eligible_tasks: List[Task] = []
                for task in candidate_tasks:
                    if self.state_store.is_duplicate_task(
                        full_name,
                        task.title,
                        task.related_files,
                        category=task.category.value if hasattr(task.category, "value") else str(task.category),
                        task_id=task.id,
                        starting_revision=starting_revision,
                    ):
                        logger.info(f"Skipping duplicate or already-processed task: '{task.title}'")
                        summary.skipped_count += 1
                        summary.tasks.append(
                            TaskExecutionSummary(
                                task_id=task.id,
                                category=task.category.value,
                                title=task.title,
                                status=TaskStatus.SKIPPED,
                            )
                        )
                    else:
                        eligible_tasks.append(task)

                # Step 8 to 13: Bounded execution per task
                tasks_to_run = eligible_tasks[: self.config.settings.max_tasks_per_repository]

                for task in tasks_to_run:
                    task_outcome = self._execute_task(
                        repo_cfg,
                        workspace_dir,
                        task,
                        ws=ws,
                        starting_revision=starting_revision,
                    )
                    summary.tasks.append(task_outcome)
                    if task_outcome.status == TaskStatus.PUBLISHED:
                        summary.success_count += 1
                    elif task_outcome.status == TaskStatus.FAILED:
                        summary.failure_count += 1
                    elif task_outcome.status == TaskStatus.SKIPPED:
                        summary.skipped_count += 1

        except Exception as e:
            logger.error(f"Error processing repository {full_name}: {e}", exc_info=True)
            summary.failure_count += 1

        summary.duration_seconds = round(time.time() - start_time, 2)
        return summary

    def _execute_task(
        self,
        repo_cfg: RepositoryConfig,
        workspace_dir: Path,
        task: Task,
        ws: Optional[IsolatedWorkspace] = None,
        starting_revision: Optional[str] = None,
    ) -> TaskExecutionSummary:
        """Execute a single task through planning, patch application, validation, and publishing."""
        full_name = f"{repo_cfg.owner}/{repo_cfg.name}"
        logger.info(f"Starting task [{task.id}]: {task.title}")
        self.state_store.record_task_start(full_name, task, starting_revision=starting_revision)

        # Take a snapshot of the clean workspace to allow guaranteed rollback on failure
        if ws:
            ws.snapshot(f"clean_{task.id}")

        current_retries = 0
        max_retries = min(self.config.settings.max_retries_per_task, task.max_retries)
        last_error = ""
        previous_patches = []
        validation_result = None
        plan = None

        while current_retries <= max_retries:
            task.retry_count = current_retries

            # If retrying, evaluate whether repair is justified
            if current_retries > 0 and last_error:
                diagnosis = self.repair_planner.evaluate_repair(
                    task=task,
                    validation_errors=last_error,
                    retry_attempt=current_retries,
                    previous_patches=previous_patches,
                )
                if not diagnosis.can_repair or diagnosis.abandon:
                    msg = f"Task repair not feasible or retry exhausted: {diagnosis.reason}"
                    logger.warning(msg)
                    task.status = TaskStatus.FAILED
                    task.error_message = msg
                    if ws:
                        ws.restore_snapshot(f"clean_{task.id}")
                    self.state_store.record_task_outcome(full_name, task)
                    return TaskExecutionSummary(
                        task_id=task.id,
                        category=task.category.value,
                        title=task.title,
                        status=TaskStatus.FAILED,
                        error=msg,
                    )

            # Gather target file contents if they exist
            file_contents: Dict[str, str] = {}
            for rel_file in task.related_files:
                file_p = workspace_dir / rel_file
                if file_p.exists() and file_p.is_file():
                    try:
                        file_contents[rel_file] = file_p.read_text(encoding="utf-8")[:10000]
                    except Exception:
                        pass

            code_req = AICodeChangeRequest(
                repo_owner=repo_cfg.owner,
                repo_name=repo_cfg.name,
                task=task,
                file_contents=file_contents,
                validation_errors=last_error if current_retries > 0 else None,
                retry_attempt=current_retries,
                excluded_paths=repo_cfg.excluded_paths,
                previous_patches=previous_patches,
                max_retries=max_retries,
            )

            if not self.ai.is_available() or self.budget.is_ai_budget_exhausted():
                msg = "AI inference is unavailable or request budget reached."
                task.status = TaskStatus.FAILED
                task.error_message = msg
                if ws:
                    ws.restore_snapshot(f"clean_{task.id}")
                self.state_store.record_task_outcome(full_name, task)
                return TaskExecutionSummary(
                    task_id=task.id,
                    category=task.category.value,
                    title=task.title,
                    status=TaskStatus.FAILED,
                    error=msg,
                )

            self.budget.record_ai_request()
            plan = self.ai.generate_code_change(code_req)

            if not plan or not plan.files:
                msg = "AI provider produced no actionable code changes."
                task.status = TaskStatus.SKIPPED
                if ws:
                    ws.restore_snapshot(f"clean_{task.id}")
                self.state_store.record_task_outcome(full_name, task)
                return TaskExecutionSummary(
                    task_id=task.id,
                    category=task.category.value,
                    title=task.title,
                    status=TaskStatus.SKIPPED,
                    error=msg,
                )

            # Verify that patch is not a repeated failed proposal
            if previous_patches and self.repair_planner.is_repeated_patch(plan, previous_patches):
                logger.warning(f"AI proposed an identical patch previously failed for {task.id}. Rejecting repeat.")
                last_error = "Repeated identical patch proposal rejected."
                current_retries += 1
                continue

            previous_patches.append(plan)

            # Step 9: Apply changes with security checks
            try:
                applier = PatchApplier(workspace_dir, repo_cfg.excluded_paths)
                applier.apply_plan(plan)
                if ws:
                    for f in plan.files:
                        ws.track_modified_file(f.path)
            except SecurityViolationError as sec_err:
                msg = f"Security boundary violation rejected: {sec_err}"
                logger.error(msg)
                task.status = TaskStatus.FAILED
                task.error_message = msg
                if ws:
                    ws.restore_snapshot(f"clean_{task.id}")
                self.state_store.record_task_outcome(full_name, task)
                return TaskExecutionSummary(
                    task_id=task.id,
                    category=task.category.value,
                    title=task.title,
                    status=TaskStatus.FAILED,
                    error=msg,
                )

            # Step 10: Validation runner
            require_val = self.config.settings.require_validation and not (
                task.category == TaskCategory.DOCUMENTATION and not repo_cfg.validation.test_command
            )
            validator = ValidationRunner(workspace_dir, repo_cfg.validation, require_validation=require_val)
            validation_result = validator.validate_task(
                task.id,
                changed_files=[f.path for f in plan.files],
            )

            if validation_result.passed:
                task.status = TaskStatus.VALIDATED
                logger.info(f"Task {task.id} passed validation.")
                self.state_store.record_checkpoint(
                    full_name,
                    task.id,
                    CheckpointStage.VALIDATION_COMPLETED,
                    {"passed": "True"},
                )
                break
            else:
                last_error = validation_result.error_summary or "Validation failed"
                logger.warning(f"Task {task.id} validation attempt {current_retries} failed: {last_error}")
                # Rollback workspace changes so retry starts from clean state
                if ws:
                    ws.restore_snapshot(f"clean_{task.id}")
                self.state_store.record_checkpoint(
                    full_name,
                    task.id,
                    CheckpointStage.CHANGE_REJECTED,
                    {"attempt": str(current_retries), "error": last_error},
                )
                current_retries += 1

        if not validation_result or not validation_result.passed:
            # Step 12: Discard changes that fail mandatory validation
            task.status = TaskStatus.FAILED
            task.error_message = f"Failed validation after {max_retries} retries: {last_error}"
            if ws:
                ws.restore_snapshot(f"clean_{task.id}")
            self.state_store.record_task_outcome(full_name, task)
            return TaskExecutionSummary(
                task_id=task.id,
                category=task.category.value,
                title=task.title,
                status=TaskStatus.FAILED,
                error=task.error_message,
            )

        # Step 13: Enforce publishing eligibility
        if not validation_result.is_publishable:
            eligibility_reason = validation_result.diagnostics.get(
                "publishing_eligibility_reason",
                "Change not eligible for publication under policy."
            )
            task.status = TaskStatus.FAILED
            task.error_message = f"Publishing blocked: {eligibility_reason}"
            logger.warning(task.error_message)
            if ws:
                ws.restore_snapshot(f"clean_{task.id}")
            self.state_store.record_task_outcome(full_name, task)
            return TaskExecutionSummary(
                task_id=task.id,
                category=task.category.value,
                title=task.title,
                status=TaskStatus.FAILED,
                error=task.error_message,
            )

        # Step 14: Publish validated changes via GitHubPublisher
        try:
            publish_result = self.publisher.publish_validated_change(
                repo_cfg=repo_cfg,
                task=task,
                plan=plan,
                validation=validation_result,
                starting_base_sha=starting_revision,
            )

            if publish_result.status in (PublishStatus.PUBLISHED, PublishStatus.UPDATED):
                task.status = TaskStatus.PUBLISHED
                task.pr_number = publish_result.pr_number
                task.pr_url = publish_result.pr_url
                task.branch_name = publish_result.branch_name
                self.state_store.record_task_outcome(full_name, task)
                return TaskExecutionSummary(
                    task_id=task.id,
                    category=task.category.value,
                    title=task.title,
                    status=TaskStatus.PUBLISHED,
                    pr_url=task.pr_url,
                )
            else:
                task.status = TaskStatus.FAILED
                task.error_message = publish_result.error_message or f"Publishing rejected ({publish_result.status.value})"
                logger.warning(f"Publishing failed for task {task.id}: {task.error_message}")
                self.state_store.record_task_outcome(full_name, task)
                return TaskExecutionSummary(
                    task_id=task.id,
                    category=task.category.value,
                    title=task.title,
                    status=TaskStatus.FAILED,
                    error=task.error_message,
                )

        except Exception as e:
            logger.error(f"Failed publishing task {task.id}: {e}", exc_info=True)
            task.status = TaskStatus.FAILED
            task.error_message = str(e)
            self.state_store.record_task_outcome(full_name, task)
            return TaskExecutionSummary(
                task_id=task.id,
                category=task.category.value,
                title=task.title,
                status=TaskStatus.FAILED,
                error=str(e),
            )
