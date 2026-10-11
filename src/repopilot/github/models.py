"""GitHub domain models and publishing interfaces."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class GitHubRepoInfo(BaseModel):
    owner: str
    name: str
    default_branch: str = "main"
    is_private: bool = False
    description: Optional[str] = None
    permissions: Dict[str, bool] = Field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class GitHubIssue(BaseModel):
    number: int
    title: str
    body: str = ""
    state: str = "open"
    labels: List[str] = Field(default_factory=list)


class GitHubPullRequest(BaseModel):
    number: int
    html_url: str
    title: str
    head_branch: str
    base_branch: str
    state: str = "open"
    draft: bool = False
    merged: bool = False
    body: str = ""
    head_sha: Optional[str] = None
    mergeable: Optional[bool] = None
    mergeable_state: Optional[str] = None  # clean, unstable, dirty, blocked, behind
    labels: List[str] = Field(default_factory=list)


class GitHubCommit(BaseModel):
    sha: str
    message: str
    author: str = ""
    tree_sha: Optional[str] = None
    parents: List[str] = Field(default_factory=list)


class GitHubBranch(BaseModel):
    name: str
    sha: str
    protected: bool = False


class GitHubRef(BaseModel):
    ref: str
    sha: str


class GitHubCheckRun(BaseModel):
    name: str
    status: str = "completed"  # queued, in_progress, completed
    conclusion: Optional[str] = "success"  # success, failure, neutral, cancelled, timed_out, action_required
    html_url: Optional[str] = None


class GitHubReview(BaseModel):
    id: int
    user: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, PENDING, DISMISSED
    submitted_at: Optional[datetime] = None


class SecretScanMatch(BaseModel):
    file_path: str
    secret_type: str
    line_number: Optional[int] = None
    snippet: str = ""


class SecurityAuditResult(BaseModel):
    is_safe: bool
    violations: List[str] = Field(default_factory=list)
    found_secrets: List[SecretScanMatch] = Field(default_factory=list)
    sensitive_areas: List[str] = Field(default_factory=list)


class ConflictResult(BaseModel):
    repository: str
    branch: str
    base_revision: str
    current_revision: str
    recoverable: bool = False
    reason: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class MergeEligibilityResult(BaseModel):
    eligible: bool
    reasons: List[str] = Field(default_factory=list)
    missing_conditions: List[str] = Field(default_factory=list)
    checks_status: str = "unknown"  # passed, failed, pending, none
    review_status: str = "unknown"  # approved, changes_requested, pending, not_required


class PublishStatus(str, Enum):
    PUBLISHED = "published"
    UPDATED = "updated"
    REJECTED = "rejected"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class PublishResult(BaseModel):
    task_id: str
    status: PublishStatus
    repository: str
    branch_name: Optional[str] = None
    commit_sha: Optional[str] = None
    pr_number: Optional[int] = None
    pr_url: Optional[str] = None
    merged: bool = False
    merge_result: Optional[MergeEligibilityResult] = None
    conflict_result: Optional[ConflictResult] = None
    error_message: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
