"""Configuration models and schema validation for RepoPilot."""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from repopilot.core.models import PublishMode, TaskCategory


DEFAULT_MANDATORY_EXCLUDED_PATHS = [
    ".github/workflows/",
    "secrets/",
    "production_credentials/",
    ".env",
]


class ValidationSettings(BaseModel):
    """Configuration for validation commands per repository."""
    test_command: Optional[str] = None
    lint_command: Optional[str] = None
    timeout_seconds: int = Field(default=180, ge=10, le=600)


class RepositoryConfig(BaseModel):
    """Configuration for a managed target repository."""
    owner: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    enabled: bool = True
    base_branch: str = "main"
    goals: List[str] = Field(default_factory=lambda: [
        "improve_code_quality",
        "fix_reproducible_bugs",
        "improve_documentation",
    ])
    allowed_tasks: List[TaskCategory] = Field(default_factory=lambda: [
        TaskCategory.BUG_FIX,
        TaskCategory.DOCUMENTATION,
        TaskCategory.TESTS,
        TaskCategory.CODE_QUALITY,
    ])
    excluded_paths: List[str] = Field(default_factory=lambda: list(DEFAULT_MANDATORY_EXCLUDED_PATHS))
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    publish_mode: PublishMode = PublishMode.PULL_REQUEST
    # Non-negotiable security requirement: auto_merge must ALWAYS be false by default
    auto_merge: bool = False

    @field_validator("excluded_paths")
    @classmethod
    def ensure_mandatory_exclusions(cls, v: List[str]) -> List[str]:
        """Ensure critical sensitive paths can never be omitted from excluded_paths."""
        merged = list(v)
        for req in DEFAULT_MANDATORY_EXCLUDED_PATHS:
            if not any(req in p or p in req for p in merged):
                merged.append(req)
        return merged

    @model_validator(mode="after")
    def validate_security_constraints(self) -> "RepositoryConfig":
        """Safety checks on repository configuration."""
        if "/" in self.owner or "\\" in self.owner:
            raise ValueError(f"Invalid owner format: '{self.owner}'")
        if "/" in self.name or "\\" in self.name:
            raise ValueError(f"Invalid name format: '{self.name}'")
        return self


class GlobalSettings(BaseModel):
    """Runtime limits and global settings to keep execution within ₹0 zero-cost bounds."""
    max_repositories_per_run: int = Field(default=10, ge=1, le=50)
    max_tasks_per_repository: int = Field(default=2, ge=1, le=10)
    max_retries_per_task: int = Field(default=2, ge=0, le=5)
    max_execution_minutes: int = Field(default=10, ge=1, le=60)
    max_ai_requests_per_run: int = Field(default=20, ge=1, le=100)
    require_validation: bool = True
    default_publish_mode: PublishMode = PublishMode.PULL_REQUEST

    # Persistence settings
    state_backend: str = "git_branch"  # "git_branch", "local_file", "mock"
    state_branch_name: str = "repopilot-state"
    state_file_path: str = ".repopilot/state.json"

    # AI Provider settings
    ai_provider: str = "mock"  # "gemini", "mock", "none", "openai_compatible"
    ai_model: str = "gemini-1.5-flash"
    ai_request_timeout_seconds: int = Field(default=30, ge=5, le=120)


class RepoPilotConfig(BaseModel):
    """Top-level schema for repos.yaml."""
    version: int = Field(default=1, ge=1, le=1)
    settings: GlobalSettings = Field(default_factory=GlobalSettings)
    repositories: List[RepositoryConfig] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"Unsupported configuration version: {v}. Must be 1.")
        return v
