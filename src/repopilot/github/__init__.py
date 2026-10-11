"""GitHub integration package exports for RepoPilot."""

from repopilot.github.auth import (
    AuthMethod,
    GitHubCredentials,
    OPERATION_PERMISSIONS,
    redact_token,
)
from repopilot.github.branch_manager import BranchManager
from repopilot.github.client import (
    GitHubClientInterface,
    MockGitHubClient,
    RestGitHubClient,
)
from repopilot.github.commit_builder import CommitBuilder
from repopilot.github.conflicts import ConflictDetector
from repopilot.github.merge_engine import MergePolicyEngine
from repopilot.github.models import (
    ConflictResult,
    GitHubBranch,
    GitHubCheckRun,
    GitHubCommit,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRef,
    GitHubRepoInfo,
    GitHubReview,
    MergeEligibilityResult,
    PublishResult,
    PublishStatus,
    SecretScanMatch,
    SecurityAuditResult,
)
from repopilot.github.pr_manager import PullRequestManager
from repopilot.github.publisher import GitHubPublisher
from repopilot.github.security import SecurityPolicyChecker

__all__ = [
    # Auth
    "AuthMethod",
    "GitHubCredentials",
    "OPERATION_PERMISSIONS",
    "redact_token",
    # Branch
    "BranchManager",
    # Client
    "GitHubClientInterface",
    "MockGitHubClient",
    "RestGitHubClient",
    # Commit
    "CommitBuilder",
    # Conflicts
    "ConflictDetector",
    # Merge
    "MergePolicyEngine",
    # Models
    "ConflictResult",
    "GitHubBranch",
    "GitHubCheckRun",
    "GitHubCommit",
    "GitHubIssue",
    "GitHubPullRequest",
    "GitHubRef",
    "GitHubRepoInfo",
    "GitHubReview",
    "MergeEligibilityResult",
    "PublishResult",
    "PublishStatus",
    "SecretScanMatch",
    "SecurityAuditResult",
    # PR
    "PullRequestManager",
    # Publisher
    "GitHubPublisher",
    # Security
    "SecurityPolicyChecker",
]
