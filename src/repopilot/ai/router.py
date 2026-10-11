"""Provider router for AI inference."""

import logging
from typing import Optional

from repopilot.ai.gemini_provider import GeminiProvider
from repopilot.ai.local_provider import LocalAIProvider
from repopilot.ai.mock_provider import MockAIProvider
from repopilot.ai.provider import AIProviderInterface
from repopilot.config.models import GlobalSettings

logger = logging.getLogger(__name__)


def get_ai_provider(settings: Optional[GlobalSettings] = None) -> AIProviderInterface:
    """Instantiate the configured AI provider, strictly maintaining zero operating cost.

    Never silently falls back to a paid inference provider.
    """
    if not settings:
        return MockAIProvider(available=True)

    provider_name = (settings.ai_provider or "mock").strip().lower()

    if provider_name == "gemini":
        return GeminiProvider(
            model=settings.ai_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    elif provider_name in ("local", "ollama", "openai_compatible"):
        return LocalAIProvider(
            model=settings.ai_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    elif provider_name in ("mock", "test"):
        return MockAIProvider(available=True)
    elif provider_name in ("none", "disabled", "unconfigured"):
        logger.info("AI provider is explicitly disabled in settings.")
        return MockAIProvider(available=False)
    else:
        logger.warning(
            f"Unknown or unsupported AI provider '{provider_name}'. "
            f"Returning unconfigured provider to prevent unauthorized or paid operations."
        )
        return MockAIProvider(available=False)
