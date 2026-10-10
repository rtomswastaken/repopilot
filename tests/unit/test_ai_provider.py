"""Unit tests for AI provider abstraction, graceful degradation, and error handling."""

from repopilot.ai.gemini_provider import GeminiProvider
from repopilot.ai.mock_provider import MockAIProvider
from repopilot.ai.provider import (
    AICodeChangeRequest,
    AIPlanRequest,
    ProviderStatus,
)
from repopilot.ai.router import get_ai_provider
from repopilot.config.models import GlobalSettings
from repopilot.core.models import Task, TaskCategory, TaskStatus


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
