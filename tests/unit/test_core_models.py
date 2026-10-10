"""Unit tests for core domain models, budget tracking, and secret redaction."""

from datetime import datetime
from repopilot.core.logging import redact_secrets
from repopilot.core.models import (
    BudgetTracker,
    ChangeAction,
    ChangePlan,
    FileChange,
    Task,
    TaskCategory,
    TaskStatus,
)


def test_budget_tracker_limits():
    budget = BudgetTracker(max_execution_minutes=10, max_ai_requests=5)
    assert budget.is_time_exhausted() is False
    assert budget.is_ai_budget_exhausted() is False

    for _ in range(5):
        budget.record_ai_request()

    assert budget.ai_requests_used == 5
    assert budget.is_ai_budget_exhausted() is True


def test_task_serialization():
    task = Task(
        id="task-101",
        repo_owner="alice",
        repo_name="demo-repo",
        category=TaskCategory.BUG_FIX,
        title="Fix null pointer exception",
        description="Handle missing key gracefully.",
        status=TaskStatus.DISCOVERED,
    )
    assert task.qualified_repo_name == "alice/demo-repo"

    data = task.model_dump()
    assert data["id"] == "task-101"
    assert data["category"] == "bug_fix"

    restored = Task.model_validate(data)
    assert restored.id == task.id
    assert restored.category == TaskCategory.BUG_FIX


def test_redact_secrets_masks_tokens():
    raw_log = "Using token ghp_123456789012345678901234567890123456 and Bearer eyJhbGciOiJIUzI1NiJ9"
    sanitized = redact_secrets(raw_log)
    assert "ghp_1234567890" not in sanitized
    assert "[REDACTED_TOKEN]" in sanitized


def test_change_plan_creation():
    change = FileChange(
        path="src/utils.py",
        action=ChangeAction.MODIFY,
        content="# updated",
        rationale="Fix edge case",
    )
    plan = ChangePlan(
        task_id="t-1",
        title="Update utils",
        description="Refactor helpers",
        files=[change],
    )
    assert len(plan.files) == 1
    assert plan.files[0].path == "src/utils.py"
