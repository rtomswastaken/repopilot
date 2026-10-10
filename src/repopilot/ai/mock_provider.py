"""Deterministic Mock AI provider for local testing and zero-cost dry runs."""

import logging
from typing import List, Optional

from repopilot.ai.provider import (
    AICodeChangeRequest,
    AIPlanRequest,
    AIProviderInterface,
    ProviderStatus,
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
    """Mock provider allowing full end-to-end execution without external credentials."""

    def __init__(self, available: bool = True):
        self._available = available

    def get_name(self) -> str:
        return "mock"

    def get_status(self) -> ProviderStatus:
        return ProviderStatus.AVAILABLE if self._available else ProviderStatus.UNCONFIGURED

    def plan_improvements(self, request: AIPlanRequest) -> List[Task]:
        """Propose deterministic safe improvement tasks based on goals and allowed tasks."""
        if not self.is_available():
            return []

        tasks: List[Task] = []

        if TaskCategory.DOCUMENTATION.value in request.allowed_tasks or "documentation" in request.allowed_tasks:
            tasks.append(
                Task(
                    id=f"{request.repo_name}-doc-01",
                    repo_owner=request.repo_owner,
                    repo_name=request.repo_name,
                    category=TaskCategory.DOCUMENTATION,
                    title="Improve README with project overview and setup instructions",
                    description="Add comprehensive documentation and badges for contributors.",
                    status=TaskStatus.DISCOVERED,
                    related_files=["README.md"],
                )
            )

        if TaskCategory.TESTS.value in request.allowed_tasks or "tests" in request.allowed_tasks:
            tasks.append(
                Task(
                    id=f"{request.repo_name}-test-01",
                    repo_owner=request.repo_owner,
                    repo_name=request.repo_name,
                    category=TaskCategory.TESTS,
                    title="Add smoke tests for core functionality",
                    description="Ensure basic initialization and exit states are covered with unit tests.",
                    status=TaskStatus.DISCOVERED,
                    related_files=["tests/test_smoke.py"],
                )
            )

        return tasks

    def generate_code_change(self, request: AICodeChangeRequest) -> Optional[ChangePlan]:
        """Generate safe, concrete changes for testing."""
        if not self.is_available():
            return None

        task = request.task

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

        if task.category == TaskCategory.TESTS:
            test_content = (
                "# Auto-generated smoke test\n"
                "def test_smoke():\n"
                "    assert 1 + 1 == 2\n"
            )
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
