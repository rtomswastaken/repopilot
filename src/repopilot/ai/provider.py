"""Abstract AI Provider interface and data structures."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from repopilot.core.models import ChangePlan, Task


class ProviderStatus(str, Enum):
    AVAILABLE = "available"
    UNCONFIGURED = "unconfigured"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ERROR = "error"


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0  # Must strictly remain 0.0 for zero-cost operation


class AIPlanRequest(BaseModel):
    repo_owner: str
    repo_name: str
    goals: List[str]
    allowed_tasks: List[str]
    tech_stack: Dict[str, Any] = Field(default_factory=dict)
    file_tree: List[str] = Field(default_factory=list)
    recent_commits: List[str] = Field(default_factory=list)
    open_issues: List[Dict[str, Any]] = Field(default_factory=list)
    # Repository context and history extensions (with default backwards-compatible values)
    repo_context: Optional[Any] = None
    readme_content: Optional[str] = None
    doc_files: List[str] = Field(default_factory=list)
    task_history: List[str] = Field(default_factory=list)
    rejected_tasks: List[str] = Field(default_factory=list)


class AICodeChangeRequest(BaseModel):
    repo_owner: str
    repo_name: str
    task: Task
    file_contents: Dict[str, str] = Field(default_factory=dict)
    validation_errors: Optional[str] = None
    retry_attempt: int = 0
    # Additional context for repair and safety bounds
    excluded_paths: List[str] = Field(default_factory=list)
    previous_patches: List[ChangePlan] = Field(default_factory=list)
    max_retries: int = 2


class AIResponse(BaseModel):
    provider_name: str
    model_name: str
    plan: Optional[ChangePlan] = None
    raw_text: Optional[str] = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AIProviderInterface(ABC):
    """Abstract interface for pluggable AI inference providers."""

    @abstractmethod
    def get_name(self) -> str:
        """Name of the provider (e.g., 'gemini', 'mock', 'local')."""
        pass

    @abstractmethod
    def get_status(self) -> ProviderStatus:
        """Check provider status (connectivity, quota, configuration)."""
        pass

    def is_available(self) -> bool:
        """Convenience method checking if inference can proceed."""
        return self.get_status() == ProviderStatus.AVAILABLE

    def get_usage(self) -> TokenUsage:
        """Return cumulative token usage for the provider instance."""
        return TokenUsage(estimated_cost_usd=0.0)

    def check_connection(self) -> bool:
        """Perform a quick health/connectivity check to verify inference availability."""
        return self.is_available()

    @abstractmethod
    def plan_improvements(self, request: AIPlanRequest) -> List[Task]:
        """Analyze repository context and propose structured, bounded candidate tasks."""
        pass

    @abstractmethod
    def generate_code_change(self, request: AICodeChangeRequest) -> Optional[ChangePlan]:
        """Generate concrete file modifications and patches for a designated task."""
        pass
