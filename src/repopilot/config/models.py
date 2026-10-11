"""Configuration models and schema validation for RepoPilot."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from repopilot.core.models import PublishMode, TaskCategory

DEFAULT_MANDATORY_EXCLUDED_PATHS = [
    ".github/workflows/",
    "secrets/",
    "production_credentials/",
    ".env",
]

# Sensitive keys that must NEVER appear in configuration files
FORBIDDEN_CREDENTIAL_KEYS = {
    "token", "github_token", "gh_token", "api_key", "gemini_api_key",
    "openai_api_key", "password", "secret", "private_key", "auth_token",
    "access_token", "bearer_token",
}

SUPPORTED_CONFIG_VERSIONS = [1]


def scan_for_credentials(data: Any, path: str = "root") -> None:
    """Recursively inspect configuration dictionary to ensure no credentials are present."""
    if isinstance(data, dict):
        for k, v in data.items():
            key_lower = str(k).lower().strip()
            # If the key itself indicates a credential
            if key_lower in FORBIDDEN_CREDENTIAL_KEYS or re.search(r"(token|secret|password|api_key|auth_key)$", key_lower):
                raise ValueError(
                    f"Credential setting '{k}' found at '{path}'. Credentials must never be stored "
                    f"in configuration files. Use environment variables (e.g. GITHUB_TOKEN, GEMINI_API_KEY) instead."
                )
            scan_for_credentials(v, f"{path}.{k}")
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            scan_for_credentials(item, f"{path}[{idx}]")


class ValidationSettings(BaseModel):
    """Configuration for validation commands per repository."""
    model_config = ConfigDict(extra="forbid")

    test_command: Optional[str] = None
    lint_command: Optional[str] = None
    timeout_seconds: int = Field(default=180, ge=1, le=600)


class ReportingSettings(BaseModel):
    """Reporting and notification options."""
    model_config = ConfigDict(extra="forbid")

    step_summary_enabled: bool = True
    json_report_path: Optional[str] = None
    issue_reporting_enabled: bool = False
    issue_title_prefix: str = "[RepoPilot Report]"
    pr_comment_reporting: bool = False
    retention_days: int = Field(default=30, ge=1, le=365)


class RepositoryConfig(BaseModel):
    """Configuration for a managed target repository."""
    model_config = ConfigDict(extra="forbid")

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
            raise ValueError(f"Invalid owner format: '{self.owner}'. Slashes are not allowed.")
        if "/" in self.name or "\\" in self.name:
            raise ValueError(f"Invalid name format: '{self.name}'. Slashes are not allowed.")
        if not self.base_branch or not self.base_branch.strip():
            raise ValueError("base_branch cannot be empty.")

        # Safety policy: auto_merge cannot be enabled without explicit safe boundaries
        if self.auto_merge:
            # Dangerous tasks cannot be auto-merged
            if not self.allowed_tasks:
                raise ValueError("auto_merge cannot be enabled when allowed_tasks is empty.")
            # Disallow wildcards in base_branch
            if "*" in self.base_branch or "?" in self.base_branch:
                raise ValueError("auto_merge cannot be enabled with wildcard base_branch.")

        return self


class GlobalSettings(BaseModel):
    """Runtime limits and global settings to keep execution within ₹0 zero-cost bounds."""
    model_config = ConfigDict(extra="forbid")

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

    # Reporting settings
    reporting: ReportingSettings = Field(default_factory=ReportingSettings)

    @field_validator("state_backend")
    @classmethod
    def validate_state_backend(cls, v: str) -> str:
        allowed = {"git_branch", "local_file", "mock"}
        if v.lower() not in allowed:
            raise ValueError(f"Invalid state_backend '{v}'. Must be one of: {', '.join(sorted(allowed))}")
        return v.lower()


class RepoPilotConfig(BaseModel):
    """Top-level schema for repos.yaml."""
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1)
    settings: GlobalSettings = Field(default_factory=GlobalSettings)
    repositories: List[RepositoryConfig] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: int) -> int:
        if v not in SUPPORTED_CONFIG_VERSIONS:
            raise ValueError(
                f"Unsupported configuration version: {v}. Supported versions: {SUPPORTED_CONFIG_VERSIONS}."
            )
        return v

    @model_validator(mode="before")
    @classmethod
    def check_credentials_presence(cls, data: Any) -> Any:
        if isinstance(data, dict):
            scan_for_credentials(data)
        return data
