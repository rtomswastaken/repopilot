"""GitHub client interface, REST client, and Mock client for zero-cost operation."""

from abc import ABC, abstractmethod
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from repopilot.core.exceptions import GitHubAPIError
from repopilot.github.models import GitHubCommit, GitHubIssue, GitHubPullRequest, GitHubRepoInfo

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubClientInterface(ABC):
    """Abstract interface for GitHub API operations."""

    @abstractmethod
    def get_repo(self, owner: str, name: str) -> GitHubRepoInfo:
        pass

    @abstractmethod
    def list_open_issues(self, owner: str, name: str) -> List[GitHubIssue]:
        pass

    @abstractmethod
    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool:
        pass

    @abstractmethod
    def create_pull_request(
        self,
        owner: str,
        name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        draft: bool = False,
    ) -> GitHubPullRequest:
        pass

    @abstractmethod
    def merge_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        commit_title: str,
    ) -> bool:
        pass


class MockGitHubClient(GitHubClientInterface):
    """Simulated in-memory GitHub client for offline development and testing."""

    def __init__(self):
        self.repos: Dict[str, GitHubRepoInfo] = {}
        self.issues: Dict[str, List[GitHubIssue]] = {}
        self.pull_requests: Dict[str, List[GitHubPullRequest]] = {}
        self.branches: Dict[str, List[str]] = {}
        self.pr_counter = 100

    def get_repo(self, owner: str, name: str) -> GitHubRepoInfo:
        key = f"{owner}/{name}"
        if key not in self.repos:
            self.repos[key] = GitHubRepoInfo(owner=owner, name=name, default_branch="main")
        return self.repos[key]

    def list_open_issues(self, owner: str, name: str) -> List[GitHubIssue]:
        key = f"{owner}/{name}"
        return self.issues.get(key, [
            GitHubIssue(
                number=1,
                title="Improve test coverage",
                body="Please add unit tests for the core logic.",
                labels=["enhancement"],
            )
        ])

    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool:
        key = f"{owner}/{name}"
        if key not in self.branches:
            self.branches[key] = [base_branch]
        self.branches[key].append(new_branch)
        logger.info(f"[MockGitHub] Created branch '{new_branch}' from '{base_branch}' in {key}")
        return True

    def create_pull_request(
        self,
        owner: str,
        name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        draft: bool = False,
    ) -> GitHubPullRequest:
        key = f"{owner}/{name}"
        self.pr_counter += 1
        pr = GitHubPullRequest(
            number=self.pr_counter,
            html_url=f"https://github.com/{owner}/{name}/pull/{self.pr_counter}",
            title=title,
            head_branch=head_branch,
            base_branch=base_branch,
            draft=draft,
            state="open",
        )
        if key not in self.pull_requests:
            self.pull_requests[key] = []
        self.pull_requests[key].append(pr)
        logger.info(f"[MockGitHub] Created PR #{pr.number}: {pr.title} ({pr.html_url})")
        return pr

    def merge_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        commit_title: str,
    ) -> bool:
        key = f"{owner}/{name}"
        prs = self.pull_requests.get(key, [])
        for pr in prs:
            if pr.number == pr_number:
                pr.merged = True
                pr.state = "closed"
                logger.info(f"[MockGitHub] Merged PR #{pr_number} in {key}")
                return True
        return False


class RestGitHubClient(GitHubClientInterface):
    """Real REST API client using GitHub Actions GITHUB_TOKEN or user PAT."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.token:
            logger.warning("No GitHub token configured. RestGitHubClient will have read-only unauthenticated limits.")

    def _request(self, endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{GITHUB_API_BASE}{endpoint}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoPilot-Autonomous-Agent/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body_bytes = None
        if data is not None:
            body_bytes = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 204:
                    return None
                resp_text = resp.read().decode("utf-8")
                return json.loads(resp_text) if resp_text else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="ignore")
            logger.error(f"GitHub API Error {e.code} on {method} {endpoint}: {msg}")
            raise GitHubAPIError(f"GitHub API Error {e.code}: {e.reason}", status_code=e.code, details={"response": msg}) from e
        except Exception as e:
            logger.error(f"GitHub API Network Error: {e}")
            raise GitHubAPIError(f"GitHub Network Error: {e}") from e

    def get_repo(self, owner: str, name: str) -> GitHubRepoInfo:
        data = self._request(f"/repos/{owner}/{name}")
        return GitHubRepoInfo(
            owner=owner,
            name=name,
            default_branch=data.get("default_branch", "main"),
            is_private=data.get("private", False),
            description=data.get("description"),
        )

    def list_open_issues(self, owner: str, name: str) -> List[GitHubIssue]:
        data = self._request(f"/repos/{owner}/{name}/issues?state=open&per_page=30")
        issues = []
        for item in data:
            if "pull_request" not in item:
                issues.append(
                    GitHubIssue(
                        number=item["number"],
                        title=item["title"],
                        body=item.get("body") or "",
                        state=item.get("state", "open"),
                        labels=[label["name"] for label in item.get("labels", [])],
                    )
                )
        return issues

    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool:
        ref_data = self._request(f"/repos/{owner}/{name}/git/ref/heads/{base_branch}")
        sha = ref_data["object"]["sha"]
        payload = {"ref": f"refs/heads/{new_branch}", "sha": sha}
        self._request(f"/repos/{owner}/{name}/git/refs", method="POST", data=payload)
        return True

    def create_pull_request(
        self,
        owner: str,
        name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str,
        draft: bool = False,
    ) -> GitHubPullRequest:
        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
            "draft": draft,
        }
        data = self._request(f"/repos/{owner}/{name}/pulls", method="POST", data=payload)
        return GitHubPullRequest(
            number=data["number"],
            html_url=data["html_url"],
            title=data["title"],
            head_branch=head_branch,
            base_branch=base_branch,
            state=data.get("state", "open"),
            draft=data.get("draft", False),
        )

    def merge_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        commit_title: str,
    ) -> bool:
        payload = {"commit_title": commit_title, "merge_method": "squash"}
        self._request(f"/repos/{owner}/{name}/pulls/{pr_number}/merge", method="PUT", data=payload)
        return True
