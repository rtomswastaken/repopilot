"""GitHub integration package exports for RepoPilot."""

from repopilot.github.models import (
    GitHubRepoInfo,
    GitHubIssue,
    GitHubPullRequest,
    GitHubCommit,
)
from repopilot.github.client import (
    GitHubClientInterface,
    MockGitHubClient,
    RestGitHubClient,
)

__all__ = [
    "GitHubRepoInfo",
    "GitHubIssue",
    "GitHubPullRequest",
    "GitHubCommit",
    "GitHubClientInterface",
    "MockGitHubClient",
    "RestGitHubClient",
]
