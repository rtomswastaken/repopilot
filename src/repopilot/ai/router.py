"""Provider router for AI inference."""

import logging
from typing import Optional

from repopilot.ai.gemini_provider import GeminiProvider
from repopilot.ai.mock_provider import MockAIProvider
from repopilot.ai.provider import AIProviderInterface
from repopilot.config.models import GlobalSettings

logger = logging.getLogger(__name__)


def get_ai_provider(settings: Optional[GlobalSettings] = None) -> AIProviderInterface:
    """Instantiate the configured AI provider, defaulting safely to Mock or graceful degradation."""
    if not settings:
        return MockAIProvider()

    provider_name = (settings.ai_provider or "mock").lower()

    if provider_name == "gemini":
        return GeminiProvider(
            model=settings.ai_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    elif provider_name in ("mock", "test"):
        return MockAIProvider(available=True)
    elif provider_name in ("none", "disabled"):
        return MockAIProvider(available=False)
    else:
        logger.warning(f"Unknown AI provider '{provider_name}'. Defaulting to mock provider.")
        return MockAIProvider()
