"""Core exceptions for RepoPilot."""

from typing import Any, Dict, Optional


class RepoPilotError(Exception):
    """Base exception for all RepoPilot errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(RepoPilotError):
    """Raised when configuration is invalid, missing, or unparseable."""
    pass


class SecurityViolationError(RepoPilotError):
    """Raised when a security boundary or policy constraint is violated."""
    pass


class AIProviderError(RepoPilotError):
    """Base exception for AI provider failures."""
    pass


class AIRateLimitError(AIProviderError):
    """Raised when an AI provider returns HTTP 429 or hits quota limits."""
    pass


class AIQuotaExhaustedError(AIProviderError):
    """Raised when the free-tier inference quota is completely depleted."""
    pass


class ExecutionTimeoutError(RepoPilotError):
    """Raised when an execution or command exceeds its allotted budget time."""
    pass


class ValidationFailureError(RepoPilotError):
    """Raised when code changes fail automated tests, linting, or safety checks."""
    pass


class StatePersistenceError(RepoPilotError):
    """Raised when reading or writing persistent state fails."""
    pass


class GitHubAPIError(RepoPilotError):
    """Raised when GitHub API operations fail or return rate-limit errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.status_code = status_code
