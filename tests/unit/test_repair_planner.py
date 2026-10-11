"""Unit tests for the bounded repair planner, failure diagnosis, and retry limits."""

import pytest

from repopilot.ai.repair_planner import RepairPlanner
from repopilot.ai.validator import SecurityProposalValidator
from repopilot.core.models import (
    ChangeAction,
    ChangePlan,
    FileChange,
    Task,
    TaskCategory,
    TaskStatus,
)


def create_sample_task(max_retries: int = 2) -> Task:
    return Task(
        id="task-repair-101",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.BUG_FIX,
        title="Fix division by zero in calculator",
        description="Handle zero denominator safely",
        max_retries=max_retries,
        related_files=["src/calculator.py"],
    )


# ==============================================================================
# 13. Repeated failed patches
# ==============================================================================

def test_detect_repeated_failed_patches():
    planner = RepairPlanner(max_retries=2)

    plan_attempt_1 = ChangePlan(
        task_id="task-repair-101",
        title="Fix zero division",
        description="Added zero check",
        files=[
            FileChange(
                path="src/calculator.py",
                action=ChangeAction.MODIFY,
                content="def divide(a, b):\n    if b == 0: return None\n    return a / b\n",
            )
        ],
    )

    plan_attempt_2_identical = ChangePlan(
        task_id="task-repair-101",
        title="Fix zero division attempt 2",
        description="Same attempt",
        files=[
            FileChange(
                path="src/calculator.py",
                action=ChangeAction.MODIFY,
                content="def divide(a, b):\n    if b == 0: return None\n    return a / b\n",
            )
        ],
    )

    plan_attempt_2_novel = ChangePlan(
        task_id="task-repair-101",
        title="Fix zero division attempt 2 novel",
        description="Raise ZeroDivisionError instead of returning None",
        files=[
            FileChange(
                path="src/calculator.py",
                action=ChangeAction.MODIFY,
                content="def divide(a, b):\n    if b == 0: raise ValueError('Zero')\n    return a / b\n",
            )
        ],
    )

    # Attempt 2 is identical to Attempt 1 -> detected as repeat
    assert planner.is_repeated_patch(plan_attempt_2_identical, [plan_attempt_1]) is True

    # Novel attempt is different -> accepted
    assert planner.is_repeated_patch(plan_attempt_2_novel, [plan_attempt_1]) is False


# ==============================================================================
# 14. Repair retry exhaustion
# ==============================================================================

def test_repair_retry_exhaustion():
    planner = RepairPlanner(max_retries=2)
    task = create_sample_task(max_retries=2)

    # Attempt 0: initial attempt
    diag_0 = planner.evaluate_repair(
        task=task,
        validation_errors="AssertionError: Expected 42, got None",
        retry_attempt=0,
    )
    assert diag_0.can_repair is True
    assert diag_0.abandon is False

    # Attempt 1: first retry
    diag_1 = planner.evaluate_repair(
        task=task,
        validation_errors="AssertionError: Expected 42, got None",
        retry_attempt=1,
    )
    assert diag_1.can_repair is True
    assert diag_1.abandon is False

    # Attempt 2: retry budget exhausted (limit is 2)
    diag_2 = planner.evaluate_repair(
        task=task,
        validation_errors="AssertionError: Expected 42, got None",
        retry_attempt=2,
    )
    assert diag_2.can_repair is False
    assert diag_2.abandon is True
    assert "Retry budget exhausted" in diag_2.reason


# ==============================================================================
# Error diagnosis capabilities
# ==============================================================================

def test_diagnose_pytest_assertion_error():
    planner = RepairPlanner(max_retries=2)
    task = create_sample_task()

    stderr = (
        "============================= FAILURES =============================\n"
        "____________________________ test_divide ___________________________\n"
        "tests/test_calculator.py:12: in test_divide\n"
        "    assert divide(10, 0) == 0\n"
        "E   AssertionError: assert None == 0\n"
        "FAILED tests/test_calculator.py::test_divide - AssertionError: assert None == 0\n"
    )

    diagnosis = planner.evaluate_repair(task=task, validation_errors=stderr, retry_attempt=1)
    assert diagnosis.can_repair is True
    assert "AssertionError" in diagnosis.likely_cause
    assert "tests/test_calculator.py" in diagnosis.suggested_files or "src/calculator.py" in diagnosis.suggested_files


def test_diagnose_unrepairable_system_error():
    planner = RepairPlanner(max_retries=2)
    task = create_sample_task()

    stderr = "OSError: [Errno 28] No space left on device while running pytest"
    diagnosis = planner.evaluate_repair(task=task, validation_errors=stderr, retry_attempt=1)
    assert diagnosis.can_repair is False
    assert diagnosis.abandon is True
    assert "external system" in diagnosis.reason.lower()


def test_prevent_test_suppression_cheating_in_repairs():
    """Verify that repair attempts cannot cheat by disabling or skipping failed tests."""
    validator = SecurityProposalValidator()

    cheating_patch = ChangePlan(
        task_id="task-repair-101",
        title="Pass tests by skipping failed test",
        description="Skip the test",
        files=[
            FileChange(
                path="tests/test_calculator.py",
                action=ChangeAction.MODIFY,
                content="@pytest.mark.skip(reason='broken')\ndef test_divide(): assert False\n",
            )
        ],
    )

    valid, reason = validator.validate_change_plan(cheating_patch)
    assert valid is False
    assert "suppress or disable tests" in reason.lower()
