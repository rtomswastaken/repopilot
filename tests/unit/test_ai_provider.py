"""Unit tests for AI provider abstraction, graceful degradation, and error handling."""

import json
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from repopilot.ai.gemini_provider import GeminiProvider
from repopilot.ai.local_provider import LocalAIProvider
from repopilot.ai.mock_provider import MockAIProvider
from repopilot.ai.models import StructuredTaskPlan
from repopilot.ai.prioritizer import TaskPrioritizer
from repopilot.ai.provider import (
    AICodeChangeRequest,
    AIPlanRequest,
    ProviderStatus,
    TokenUsage,
)
from repopilot.ai.router import get_ai_provider
from repopilot.ai.validator import SecurityProposalValidator
from repopilot.config.models import GlobalSettings
from repopilot.core.exceptions import (
    AIQuotaExhaustedError,
    AIRateLimitError,
    ExecutionTimeoutError,
    SecurityViolationError,
)
from repopilot.core.models import (
    ChangeAction,
    ChangePlan,
    FileChange,
    Task,
    TaskCategory,
    TaskStatus,
)


def test_mock_ai_provider_lifecycle():
    provider = MockAIProvider(available=True)
    assert provider.get_status() == ProviderStatus.AVAILABLE
    assert provider.is_available() is True

    plan_req = AIPlanRequest(
        repo_owner="org",
        repo_name="demo",
        goals=["improve_documentation"],
        allowed_tasks=["documentation"],
    )
    tasks = provider.plan_improvements(plan_req)
    assert len(tasks) > 0
    assert tasks[0].category == TaskCategory.DOCUMENTATION

    code_req = AICodeChangeRequest(
        repo_owner="org",
        repo_name="demo",
        task=tasks[0],
    )
    plan = provider.generate_code_change(code_req)
    assert plan is not None
    assert len(plan.files) > 0


def test_unconfigured_ai_provider_does_not_crash():
    provider = MockAIProvider(available=False)
    assert provider.get_status() == ProviderStatus.UNCONFIGURED
    assert provider.is_available() is False

    plan_req = AIPlanRequest(
        repo_owner="org",
        repo_name="demo",
        goals=["improve_documentation"],
        allowed_tasks=["documentation"],
    )
    tasks = provider.plan_improvements(plan_req)
    assert tasks == []


def test_gemini_provider_unconfigured_status():
    provider = GeminiProvider(api_key=None)
    assert provider.get_status() == ProviderStatus.UNCONFIGURED
    assert provider.is_available() is False


def test_router_fallback():
    settings = GlobalSettings(ai_provider="non_existent_provider")
    provider = get_ai_provider(settings)
    assert isinstance(provider, MockAIProvider)
    # Safe unconfigured status to prevent unintended operations
    assert provider.get_status() == ProviderStatus.UNCONFIGURED


# ==============================================================================
# 1. Valid structured planning responses
# ==============================================================================

def test_valid_structured_planning_response():
    provider = GeminiProvider(api_key="test-api-key")

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "tasks": [
                                    {
                                        "id": "task-bug-123",
                                        "category": "bug_fix",
                                        "title": "Fix off-by-one error in pagination",
                                        "description": "Adjust upper index bound in paginate helper.",
                                        "problem_or_opportunity": "Last page is dropped when total count divides evenly.",
                                        "evidence": "Reported in issue #42 and unit test failure.",
                                        "expected_benefit": "Ensures all items are viewable in list.",
                                        "relevant_files": ["src/repopilot/utils/pagination.py"],
                                        "implementation_steps": ["Change range bound", "Run test_pagination.py"],
                                        "required_tests": ["pytest tests/test_pagination.py"],
                                        "risk_classification": "low",
                                        "estimated_complexity": "trivial",
                                        "validation_criteria": ["All pages returned in order"],
                                        "abandon_conditions": ["If pagination schema is deprecated"],
                                    }
                                ]
                            })
                        }
                    ]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 250,
            "candidatesTokenCount": 180,
            "totalTokenCount": 430,
        },
    }

    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["fix_reproducible_bugs"],
        allowed_tasks=["bug_fix"],
        file_tree=["src/repopilot/utils/pagination.py"],
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_gemini_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        tasks = provider.plan_improvements(req)

        assert len(tasks) == 1
        task = tasks[0]
        assert task.id == "task-bug-123"
        assert task.category == TaskCategory.BUG_FIX
        assert task.title == "Fix off-by-one error in pagination"
        assert "Last page is dropped" in task.description
        assert "src/repopilot/utils/pagination.py" in task.related_files
        assert provider.get_usage().total_tokens == 430
        assert provider.get_usage().estimated_cost_usd == 0.0


# ==============================================================================
# 2. Malformed AI responses
# ==============================================================================

def test_malformed_ai_response_json():
    provider = GeminiProvider(api_key="test-api-key")

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "INVALID JSON RAW OUTPUT {not valid: 123"}]
                }
            }
        ]
    }

    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
        file_tree=["src/main.py"],
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_gemini_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        # Should handle malformed JSON cleanly and return empty list without crashing
        tasks = provider.plan_improvements(req)
        assert tasks == []


def test_malformed_ai_code_generation_response():
    provider = GeminiProvider(api_key="test-api-key")

    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "```markdown\nHere is some code but not JSON:\ndef foo(): pass\n```"}]
                }
            }
        ]
    }

    task = Task(
        id="t-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.CODE_QUALITY,
        title="Refactor helper",
        description="Refactor",
    )
    req = AICodeChangeRequest(
        repo_owner="owner",
        repo_name="repo",
        task=task,
        file_contents={"src/main.py": "def foo(): pass\n"},
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_gemini_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        plan = provider.generate_code_change(req)
        assert plan is None


# ==============================================================================
# 3. Missing required fields
# ==============================================================================

def test_missing_required_fields_in_planning():
    provider = GeminiProvider(api_key="test-api-key")

    # Tasks missing title and category
    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps({"tasks": [{"id": "t-missing"}]})}]
                }
            }
        ]
    }

    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
        file_tree=["src/main.py"],
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_gemini_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        tasks = provider.plan_improvements(req)
        assert len(tasks) == 1
        assert tasks[0].title != ""  # Defaults safely
        assert tasks[0].category == TaskCategory.CODE_QUALITY


def test_missing_required_files_in_code_generation():
    provider = GeminiProvider(api_key="test-api-key")

    # Generated plan missing files list
    mock_gemini_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps({"title": "Done", "description": "Did it", "files": []})}]
                }
            }
        ]
    }

    task = Task(
        id="t-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.CODE_QUALITY,
        title="Refactor helper",
        description="Refactor",
    )
    req = AICodeChangeRequest(
        repo_owner="owner",
        repo_name="repo",
        task=task,
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_gemini_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        plan = provider.generate_code_change(req)
        # Validator rejects empty files list
        assert plan is None


# ==============================================================================
# 4. Unavailable providers
# ==============================================================================

def test_unavailable_providers():
    gemini = GeminiProvider(api_key=None)
    assert gemini.is_available() is False
    assert gemini.get_status() == ProviderStatus.UNCONFIGURED
    assert gemini.check_connection() is False

    mock_unavail = MockAIProvider(available=False)
    assert mock_unavail.is_available() is False
    assert mock_unavail.get_status() == ProviderStatus.UNCONFIGURED

    # Planning on unavailable provider returns empty without crashing
    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
    )
    assert gemini.plan_improvements(req) == []
    assert mock_unavail.plan_improvements(req) == []


# ==============================================================================
# 5. Request timeouts
# ==============================================================================

def test_request_timeout_raises_cleanly():
    provider = GeminiProvider(api_key="test-key", timeout_seconds=5)

    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
        file_tree=["src/app.py"],
    )

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        with pytest.raises(ExecutionTimeoutError) as exc_info:
            provider.plan_improvements(req)
        assert "timed out" in str(exc_info.value).lower()


# ==============================================================================
# 6. Rate-limit responses
# ==============================================================================

def test_rate_limit_response_http_429():
    provider = GeminiProvider(api_key="test-key")

    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
        file_tree=["src/app.py"],
    )

    http_error = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(AIRateLimitError):
            provider.plan_improvements(req)
        assert provider.get_status() == ProviderStatus.RATE_LIMITED


# ==============================================================================
# 7. Exhausted usage budgets
# ==============================================================================

def test_exhausted_usage_budget_http_403():
    provider = GeminiProvider(api_key="test-key")

    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
        file_tree=["src/app.py"],
    )

    http_error = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com",
        code=403,
        msg="Forbidden - Quota Exceeded",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(AIQuotaExhaustedError):
            provider.plan_improvements(req)
        assert provider.get_status() == ProviderStatus.QUOTA_EXHAUSTED


def test_zero_cost_token_accounting():
    provider = MockAIProvider(available=True)
    req = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_documentation"],
        allowed_tasks=["documentation"],
    )
    provider.plan_improvements(req)
    usage = provider.get_usage()
    assert usage.total_tokens > 0
    assert usage.estimated_cost_usd == 0.0


# ==============================================================================
# 8. Duplicate-task detection
# ==============================================================================

def test_duplicate_task_detection_in_prioritizer():
    task1 = Task(
        id="t-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.DOCUMENTATION,
        title="Add README setup guide",
        description="Add setup instructions",
        related_files=["README.md"],
    )
    task2 = Task(
        id="t-2",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.DOCUMENTATION,
        title="Add README setup guide",  # Duplicate title
        description="Same task",
        related_files=["README.md"],
    )
    task3 = Task(
        id="t-3",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.TESTS,
        title="Add unit tests for auth",
        description="Add test cases",
        related_files=["tests/test_auth.py"],
    )

    # 1. Deduplication within batch
    filtered = TaskPrioritizer.filter_and_prioritize([task1, task2, task3])
    assert len(filtered) == 2
    # Tests has higher priority than documentation
    assert filtered[0].category == TaskCategory.TESTS
    assert filtered[1].category == TaskCategory.DOCUMENTATION

    # 2. Deduplication against task history
    history = {"add readme setup guide"}
    filtered_history = TaskPrioritizer.filter_and_prioritize([task1, task3], task_history_signatures=history)
    assert len(filtered_history) == 1
    assert filtered_history[0].id == "t-3"


# ==============================================================================
# 9. Missing repository context
# ==============================================================================

def test_missing_repository_context_yields_empty_plan():
    provider = GeminiProvider(api_key="test-key")

    req_empty_context = AIPlanRequest(
        repo_owner="owner",
        repo_name="repo",
        goals=["improve_code_quality"],
        allowed_tasks=["code_quality"],
        file_tree=[],
        readme_content=None,
        open_issues=[],
    )

    # Missing repository context prevents inventing facts
    tasks = provider.plan_improvements(req_empty_context)
    assert tasks == []


# ==============================================================================
# 10. Prohibited task proposals
# ==============================================================================

def test_prohibited_task_proposals_rejected():
    validator = SecurityProposalValidator()

    # Task attempting to modify excluded workflow
    task_workflow = Task(
        id="bad-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.CODE_QUALITY,
        title="Update CI workflow",
        description="Modify workflows",
        related_files=[".github/workflows/scheduled.yaml"],
    )
    valid, reason = validator.validate_task_proposal(task_workflow)
    assert valid is False
    assert "excluded or prohibited" in reason

    # Task with empty title
    task_empty = Task(
        id="bad-2",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.CODE_QUALITY,
        title="",
        description="Desc",
    )
    valid, reason = validator.validate_task_proposal(task_empty)
    assert valid is False


# ==============================================================================
# 11. Excluded-path enforcement
# ==============================================================================

def test_excluded_path_enforcement():
    validator = SecurityProposalValidator(excluded_paths=[".env", "secrets/", "production_credentials/"])

    # Path traversal attack
    traversal_plan = ChangePlan(
        task_id="t-sec",
        title="Hack",
        description="Escape root",
        files=[FileChange(path="../../etc/shadow", action=ChangeAction.MODIFY, content="bad")],
    )
    valid, reason = validator.validate_change_plan(traversal_plan)
    assert valid is False
    assert "traversal" in reason.lower()

    # Excluded secrets directory
    secrets_plan = ChangePlan(
        task_id="t-sec",
        title="Edit secret",
        description="Secrets",
        files=[FileChange(path="secrets/api_token.txt", action=ChangeAction.MODIFY, content="leaked")],
    )
    valid, reason = validator.validate_change_plan(secrets_plan)
    assert valid is False
    assert "forbidden excluded path" in reason.lower()

    # Excluded .env file
    env_plan = ChangePlan(
        task_id="t-sec",
        title="Edit env",
        description="Env",
        files=[FileChange(path=".env", action=ChangeAction.MODIFY, content="SECRET=1")],
    )
    valid, reason = validator.validate_change_plan(env_plan)
    assert valid is False
    assert "forbidden excluded path" in reason.lower()


# ==============================================================================
# 12. Oversized patches
# ==============================================================================

def test_oversized_patches_rejected():
    validator = SecurityProposalValidator(max_files_per_patch=3, max_file_size_bytes=500)

    # Too many files
    too_many_files = ChangePlan(
        task_id="t-size",
        title="Big rewrite",
        description="Touches too many files",
        files=[
            FileChange(path=f"file_{i}.py", action=ChangeAction.MODIFY, content="x = 1")
            for i in range(5)
        ],
    )
    valid, reason = validator.validate_change_plan(too_many_files)
    assert valid is False
    assert "maximum allowed files" in reason

    # Single file too large
    huge_file = ChangePlan(
        task_id="t-size",
        title="Huge file",
        description="Huge",
        files=[FileChange(path="large.py", action=ChangeAction.MODIFY, content="A" * 1000)],
    )
    valid, reason = validator.validate_change_plan(huge_file)
    assert valid is False
    assert "exceeds maximum size budget" in reason


# ==============================================================================
# 15. Empty or unnecessary task plans
# ==============================================================================

def test_empty_or_unnecessary_task_plan():
    validator = SecurityProposalValidator()

    empty_plan = ChangePlan(
        task_id="t-empty",
        title="No-op",
        description="Does nothing",
        files=[],
    )
    valid, reason = validator.validate_change_plan(empty_plan)
    assert valid is False
    assert "contains no file modifications" in reason


def test_paid_provider_strictly_blocked():
    """Verify that attempting to configure a paid commercial API is strictly prohibited."""
    with pytest.raises(SecurityViolationError) as exc_info:
        LocalAIProvider(base_url="https://api.openai.com/v1")
    assert "zero-cost violation" in str(exc_info.value).lower()
