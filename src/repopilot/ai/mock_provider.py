"""Deterministic Mock AI provider for local testing and zero-cost dry runs."""

import logging
from typing import List, Optional

from repopilot.ai.models import StructuredTaskPlan
from repopilot.ai.prioritizer import TaskPrioritizer
from repopilot.ai.provider import (
    AICodeChangeRequest,
    AIPlanRequest,
    AIProviderInterface,
    ProviderStatus,
    TokenUsage,
)
from repopilot.ai.repair_planner import RepairPlanner
from repopilot.ai.validator import SecurityProposalValidator
from repopilot.core.exceptions import (
    AIQuotaExhaustedError,
    AIRateLimitError,
    ExecutionTimeoutError,
)
from repopilot.core.models import (
    ChangeAction,
    ChangePlan,
    FileChange,
    Task,
    TaskCategory,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class MockAIProvider(AIProviderInterface):
    """Mock provider allowing full deterministic testing and simulation of all AI edge cases."""

    def __init__(
        self,
        available: bool = True,
        simulate_rate_limit: bool = False,
        simulate_quota_exhausted: bool = False,
        simulate_timeout: bool = False,
        simulate_malformed: bool = False,
        simulate_repeat_patch: bool = False,
        simulate_oversized_patch: bool = False,
        simulate_prohibited_proposal: bool = False,
        simulate_missing_context: bool = False,
        custom_planned_tasks: Optional[List[Task]] = None,
        custom_change_plan: Optional[ChangePlan] = None,
    ):
        self._available = available
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_quota_exhausted = simulate_quota_exhausted
        self.simulate_timeout = simulate_timeout
        self.simulate_malformed = simulate_malformed
        self.simulate_repeat_patch = simulate_repeat_patch
        self.simulate_oversized_patch = simulate_oversized_patch
        self.simulate_prohibited_proposal = simulate_prohibited_proposal
        self.simulate_missing_context = simulate_missing_context
        self.custom_planned_tasks = custom_planned_tasks
        self.custom_change_plan = custom_change_plan
        self.usage = TokenUsage(estimated_cost_usd=0.0)
        self.validator = SecurityProposalValidator()
        self.repair_planner = RepairPlanner()

    def get_name(self) -> str:
        return "mock"

    def get_status(self) -> ProviderStatus:
        if not self._available:
            return ProviderStatus.UNCONFIGURED
        if self.simulate_quota_exhausted:
            return ProviderStatus.QUOTA_EXHAUSTED
        if self.simulate_rate_limit:
            return ProviderStatus.RATE_LIMITED
        return ProviderStatus.AVAILABLE

    def get_usage(self) -> TokenUsage:
        return self.usage

    def check_connection(self) -> bool:
        return self.is_available()

    def plan_improvements(self, request: AIPlanRequest) -> List[Task]:
        """Propose deterministic safe improvement tasks based on goals and allowed tasks."""
        if self.simulate_rate_limit:
            raise AIRateLimitError("Simulated rate limit (HTTP 429).")
        if self.simulate_quota_exhausted:
            raise AIQuotaExhaustedError("Simulated quota exhaustion (HTTP 403).")
        if self.simulate_timeout:
            raise ExecutionTimeoutError("Simulated provider timeout after 30s.")
        if self.simulate_malformed:
            return []

        if not self.is_available():
            return []

        if self.simulate_missing_context and not request.file_tree and not request.readme_content and not request.open_issues:
            logger.info("MockAIProvider: Missing repository context. Returning no plans.")
            return []

        # Record mock usage
        self.usage.prompt_tokens += 120
        self.usage.completion_tokens += 80
        self.usage.total_tokens += 200

        if self.custom_planned_tasks is not None:
            return self.custom_planned_tasks

        if self.simulate_prohibited_proposal:
            # Propose a task that violates security rules (targets excluded workflows path)
            return [
                Task(
                    id=f"{request.repo_name}-prohibited-01",
                    repo_owner=request.repo_owner,
                    repo_name=request.repo_name,
                    category=TaskCategory.CODE_QUALITY,
                    title="Modify forbidden GitHub workflow",
                    description="Attempts to modify CI workflow.",
                    status=TaskStatus.DISCOVERED,
                    related_files=[".github/workflows/scheduled.yaml"],
                )
            ]

        tasks: List[Task] = []

        # 1. Bug Fix: check open issues or bug goals
        if (TaskCategory.BUG_FIX.value in request.allowed_tasks or "bug_fix" in request.allowed_tasks) and request.open_issues:
            first_issue = request.open_issues[0]
            issue_num = first_issue.get("number")
            issue_title = first_issue.get("title", "Fix reproducible issue")
            plan = StructuredTaskPlan(
                id=f"{request.repo_name}-bug-01",
                category=TaskCategory.BUG_FIX,
                title=f"Fix issue #{issue_num}: {issue_title}",
                description=f"Resolve bug reported in issue #{issue_num}.",
                problem_or_opportunity=f"Issue #{issue_num} describes an unhandled error state.",
                evidence=f"Reported in GitHub issue #{issue_num}.",
                expected_benefit="Restores expected runtime behavior.",
                relevant_files=["src/repopilot/core/models.py"],
                implementation_steps=["Inspect error condition", "Add defensive check", "Run regression tests"],
                required_tests=["pytest tests/unit/"],
                risk_classification="low",
                estimated_complexity="low",
                related_issue_number=issue_num,
            )
            tasks.append(plan.to_core_task(request.repo_owner, request.repo_name))

        # 2. Tests
        if TaskCategory.TESTS.value in request.allowed_tasks or "tests" in request.allowed_tasks:
            plan = StructuredTaskPlan(
                id=f"{request.repo_name}-test-01",
                category=TaskCategory.TESTS,
                title="Add smoke tests for core functionality",
                description="Ensure basic initialization and exit states are covered with unit tests.",
                problem_or_opportunity="Core components lack automated smoke test coverage.",
                evidence="File inspection indicates absence of tests/test_smoke.py.",
                expected_benefit="Increases regression safety during continuous deployment.",
                relevant_files=["tests/test_smoke.py"],
                implementation_steps=["Create test_smoke.py", "Define test assertions", "Execute pytest"],
                required_tests=["pytest tests/test_smoke.py"],
                risk_classification="low",
                estimated_complexity="low",
            )
            tasks.append(plan.to_core_task(request.repo_owner, request.repo_name))

        # 3. Documentation
        if TaskCategory.DOCUMENTATION.value in request.allowed_tasks or "documentation" in request.allowed_tasks:
            plan = StructuredTaskPlan(
                id=f"{request.repo_name}-doc-01",
                category=TaskCategory.DOCUMENTATION,
                title="Improve README with project overview and setup instructions",
                description="Add comprehensive documentation and badges for contributors.",
                problem_or_opportunity="README is missing deployment and usage instructions.",
                evidence="Inspected README.md content.",
                expected_benefit="Improves developer onboarding and setup clarity.",
                relevant_files=["README.md"],
                implementation_steps=["Update README.md with usage guide"],
                required_tests=["Markdown syntax validation"],
                risk_classification="low",
                estimated_complexity="low",
            )
            tasks.append(plan.to_core_task(request.repo_owner, request.repo_name))

        # Filter out duplicates and prioritize according to standards
        return TaskPrioritizer.filter_and_prioritize(
            tasks,
            allowed_categories=request.allowed_tasks,
            task_history_signatures=set(request.task_history),
            rejected_task_signatures=set(request.rejected_tasks),
        )

    def generate_code_change(self, request: AICodeChangeRequest) -> Optional[ChangePlan]:
        """Generate safe, concrete changes for testing."""
        if self.simulate_rate_limit:
            raise AIRateLimitError("Simulated rate limit (HTTP 429).")
        if self.simulate_quota_exhausted:
            raise AIQuotaExhaustedError("Simulated quota exhaustion (HTTP 403).")
        if self.simulate_timeout:
            raise ExecutionTimeoutError("Simulated provider timeout.")
        if self.simulate_malformed:
            return None

        if not self.is_available():
            return None

        self.usage.prompt_tokens += 150
        self.usage.completion_tokens += 90
        self.usage.total_tokens += 240

        if self.custom_change_plan is not None:
            return self.custom_change_plan

        task = request.task

        # Simulate repair check
        if request.retry_attempt > 0 and request.validation_errors:
            diagnosis = self.repair_planner.evaluate_repair(
                task=task,
                validation_errors=request.validation_errors,
                retry_attempt=request.retry_attempt,
                previous_patches=request.previous_patches,
            )
            if not diagnosis.can_repair:
                return None

        if self.simulate_oversized_patch:
            # Generate patch with 10 files (exceeds default max 5)
            files = [
                FileChange(path=f"file_{i}.txt", action=ChangeAction.CREATE, content="hello")
                for i in range(10)
            ]
            plan = ChangePlan(
                task_id=task.id,
                title="Oversized Patch",
                description="Touches too many files",
                files=files,
            )
            valid, reason = self.validator.validate_change_plan(plan)
            if not valid:
                logger.warning(f"Oversized patch rejected by validator: {reason}")
                return None
            return plan

        if self.simulate_repeat_patch and request.previous_patches:
            # Return identical patch as previous attempt
            return request.previous_patches[0]

        if task.category == TaskCategory.DOCUMENTATION:
            readme_content = request.file_contents.get("README.md", "# " + task.repo_name + "\n")
            doc_addition = "\n\n## Automated Quality Assurance\nMaintained with RepoPilot zero-cost autonomous operations.\n"
            new_content = readme_content + doc_addition

            return ChangePlan(
                task_id=task.id,
                title=task.title,
                description=task.description,
                files=[
                    FileChange(
                        path="README.md",
                        action=ChangeAction.MODIFY,
                        content=new_content,
                        rationale="Document RepoPilot autonomous maintenance.",
                    )
                ],
                risk_level="low",
                estimated_impact="Improves onboarding and transparency.",
            )

        if task.category in (TaskCategory.TESTS, TaskCategory.BUG_FIX):
            test_content = (
                "# Auto-generated smoke test\n"
                "def test_smoke():\n"
                "    assert 1 + 1 == 2\n"
            )
            # If retrying, produce a modified content to demonstrate repair progression
            if request.retry_attempt > 0:
                test_content += "# Repaired attempt fix\ndef test_repaired():\n    assert True\n"

            return ChangePlan(
                task_id=task.id,
                title=task.title,
                description=task.description,
                files=[
                    FileChange(
                        path="tests/test_smoke.py",
                        action=ChangeAction.CREATE,
                        content=test_content,
                        rationale="Adds smoke test verification.",
                    )
                ],
                risk_level="low",
                estimated_impact="Increases test coverage.",
            )

        return None
