"""Core domain models for RepoPilot."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class TaskCategory(str, Enum):
    BUG_FIX = "bug_fix"
    DOCUMENTATION = "documentation"
    TESTS = "tests"
    CODE_QUALITY = "code_quality"
    TYPE_ANNOTATION = "type_annotation"
    REFACTOR = "refactor"
    MAINTENANCE = "maintenance"


class TaskStatus(str, Enum):
    DISCOVERED = "discovered"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    VALIDATING = "validating"
    VALIDATED = "validated"
    PUBLISHED = "published"
    RETRYING = "retrying"
    FAILED = "failed"
    SKIPPED = "skipped"


class PublishMode(str, Enum):
    PULL_REQUEST = "pull_request"
    DRAFT_PR = "draft_pr"
    BRANCH_ONLY = "branch_only"


class ChangeAction(str, Enum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"


class FileChange(BaseModel):
    """Specification of a single file modification."""
    path: str
    action: ChangeAction = ChangeAction.MODIFY
    content: Optional[str] = None
    diff: Optional[str] = None
    rationale: str = ""


class ChangePlan(BaseModel):
    """Structured plan for code and documentation changes."""
    task_id: str
    title: str
    description: str
    files: List[FileChange] = Field(default_factory=list)
    risk_level: str = "low"  # low, medium, high
    estimated_impact: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ValidationCheck(BaseModel):
    """Outcome of a single validation command (e.g. test, lint)."""
    name: str
    passed: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    command: Optional[str] = None
    timeout: bool = False
    failure_category: Optional[str] = None
    tests_run: Optional[int] = None
    tests_passed: Optional[int] = None
    tests_failed: Optional[int] = None


class ValidationResult(BaseModel):
    """Aggregate result of all validation steps on a proposed change."""
    task_id: str
    passed: bool
    checks: List[ValidationCheck] = Field(default_factory=list)
    error_summary: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    failure_category: Optional[str] = None
    recoverable: bool = False
    is_publishable: bool = False
    has_tests: bool = True
    changed_files: List[str] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)



class Task(BaseModel):
    """An autonomous unit of work identified for a repository."""
    id: str
    repo_owner: str
    repo_name: str
    category: TaskCategory
    title: str
    description: str
    status: TaskStatus = TaskStatus.DISCOVERED
    retry_count: int = 0
    max_retries: int = 2
    related_files: List[str] = Field(default_factory=list)
    related_issue_number: Optional[int] = None
    plan: Optional[ChangePlan] = None
    validation: Optional[ValidationResult] = None
    branch_name: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def qualified_repo_name(self) -> str:
        return f"{self.repo_owner}/{self.repo_name}"


class RepositoryTarget(BaseModel):
    """Target repository metadata and rules."""
    owner: str
    name: str
    enabled: bool = True
    base_branch: str = "main"
    goals: List[str] = Field(default_factory=list)
    allowed_tasks: List[TaskCategory] = Field(default_factory=list)
    excluded_paths: List[str] = Field(default_factory=list)
    auto_merge: bool = False
    publish_mode: PublishMode = PublishMode.PULL_REQUEST

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class BudgetTracker(BaseModel):
    """Tracks consumption against execution limits to enforce zero-cost boundaries."""
    max_execution_minutes: int = 10
    max_ai_requests: int = 20
    max_tasks_per_repo: int = 2
    max_repositories: int = 10
    ai_requests_used: int = 0
    tasks_executed: int = 0
    repositories_processed: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)

    def is_ai_budget_exhausted(self) -> bool:
        return self.ai_requests_used >= self.max_ai_requests

    def record_ai_request(self) -> None:
        self.ai_requests_used += 1

    def elapsed_minutes(self) -> float:
        return (datetime.utcnow() - self.started_at).total_seconds() / 60.0

    def is_time_exhausted(self) -> bool:
        return self.elapsed_minutes() >= self.max_execution_minutes


class TaskExecutionSummary(BaseModel):
    task_id: str
    category: str
    title: str
    status: TaskStatus
    pr_url: Optional[str] = None
    error: Optional[str] = None


class RepoExecutionSummary(BaseModel):
    repository: str
    tasks: List[TaskExecutionSummary] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    duration_seconds: float = 0.0


class ExecutionReport(BaseModel):
    """Comprehensive summary produced at the conclusion of an autonomous run."""
    run_id: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    total_repositories: int = 0
    processed_repositories: int = 0
    repositories: List[RepoExecutionSummary] = Field(default_factory=list)
    total_ai_requests: int = 0
    total_changes_published: int = 0
    state_saved: bool = False
    errors: List[str] = Field(default_factory=list)
