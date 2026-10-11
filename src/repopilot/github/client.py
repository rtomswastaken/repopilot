"""GitHub client interface, REST client, and Mock client with rate limiting and resilience."""

from abc import ABC, abstractmethod
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from repopilot.core.exceptions import GitHubAPIError
from repopilot.github.auth import GitHubCredentials, redact_token
from repopilot.github.models import (
    GitHubBranch,
    GitHubCheckRun,
    GitHubCommit,
    GitHubIssue,
    GitHubPullRequest,
    GitHubRef,
    GitHubRepoInfo,
    GitHubReview,
)

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
    def get_branch(self, owner: str, name: str, branch: str) -> Optional[GitHubBranch]:
        pass

    @abstractmethod
    def get_ref(self, owner: str, name: str, ref: str) -> Optional[GitHubRef]:
        pass

    @abstractmethod
    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool:
        pass

    @abstractmethod
    def create_blob(self, owner: str, name: str, content: str) -> str:
        pass

    @abstractmethod
    def create_tree(self, owner: str, name: str, base_tree_sha: str, files: List[Dict[str, Any]]) -> str:
        pass

    @abstractmethod
    def create_commit(
        self,
        owner: str,
        name: str,
        message: str,
        tree_sha: str,
        parents: List[str],
    ) -> GitHubCommit:
        pass

    @abstractmethod
    def update_ref(self, owner: str, name: str, ref: str, sha: str, force: bool = False) -> bool:
        pass

    @abstractmethod
    def list_pull_requests(
        self,
        owner: str,
        name: str,
        state: str = "open",
        head: Optional[str] = None,
        base: Optional[str] = None,
    ) -> List[GitHubPullRequest]:
        pass

    @abstractmethod
    def get_pull_request(self, owner: str, name: str, pr_number: int) -> Optional[GitHubPullRequest]:
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
    def update_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
    ) -> GitHubPullRequest:
        pass

    @abstractmethod
    def list_check_runs(self, owner: str, name: str, ref: str) -> List[GitHubCheckRun]:
        pass

    @abstractmethod
    def list_reviews(self, owner: str, name: str, pr_number: int) -> List[GitHubReview]:
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

    @abstractmethod
    def add_labels(self, owner: str, name: str, issue_or_pr_number: int, labels: List[str]) -> List[str]:
        pass

    @abstractmethod
    def check_rate_limit(self) -> Dict[str, Any]:
        pass


class MockGitHubClient(GitHubClientInterface):
    """Simulated in-memory GitHub client for offline development and rigorous unit testing."""

    def __init__(self):
        self.repos: Dict[str, GitHubRepoInfo] = {}
        self.issues: Dict[str, List[GitHubIssue]] = {}
        self.pull_requests: Dict[str, List[GitHubPullRequest]] = {}
        self.branches: Dict[str, Dict[str, str]] = {}  # repo -> {branch_name: commit_sha}
        self.commits: Dict[str, Dict[str, GitHubCommit]] = {}  # repo -> {commit_sha: commit}
        self.check_runs: Dict[str, List[GitHubCheckRun]] = {}  # "repo:ref" -> check_runs
        self.reviews: Dict[str, List[GitHubReview]] = {}  # "repo:pr_number" -> reviews
        self.labels: Dict[str, List[str]] = {}  # "repo:pr_number" -> labels
        self.pr_counter = 100
        self.commit_counter = 1000

        # Simulation flags for test scenarios
        self.simulate_rate_limit = False
        self.simulate_auth_failure = False
        self.simulate_permission_denied = False
        self.simulate_server_error_count = 0
        self.server_error_calls_received = 0
        self.simulate_merge_conflict = False

    def _get_key(self, owner: str, name: str) -> str:
        return f"{owner}/{name}"

    def get_repo(self, owner: str, name: str) -> GitHubRepoInfo:
        if self.simulate_auth_failure:
            raise GitHubAPIError("Bad credentials or expired token", status_code=401)
        if self.simulate_rate_limit:
            raise GitHubAPIError("API rate limit exceeded", status_code=403, details={"rate_limited": True})

        key = self._get_key(owner, name)
        if key not in self.repos:
            self.repos[key] = GitHubRepoInfo(
                owner=owner,
                name=name,
                default_branch="main",
                permissions={"push": not self.simulate_permission_denied, "pull": True, "admin": False},
            )
        # Ensure default branch exists in branches map
        if key not in self.branches:
            self.branches[key] = {"main": "0000000000000000000000000000000000000001"}
        return self.repos[key]

    def list_open_issues(self, owner: str, name: str) -> List[GitHubIssue]:
        if self.simulate_auth_failure:
            raise GitHubAPIError("Bad credentials or expired token", status_code=401)
        key = self._get_key(owner, name)
        return self.issues.get(key, [
            GitHubIssue(
                number=1,
                title="Improve test coverage",
                body="Please add unit tests for the core logic.",
                labels=["enhancement"],
            )
        ])

    def get_branch(self, owner: str, name: str, branch: str) -> Optional[GitHubBranch]:
        key = self._get_key(owner, name)
        self.get_repo(owner, name)  # Ensure repo init
        if key in self.branches and branch in self.branches[key]:
            return GitHubBranch(name=branch, sha=self.branches[key][branch])
        return None

    def get_ref(self, owner: str, name: str, ref: str) -> Optional[GitHubRef]:
        branch_name = ref.replace("heads/", "").replace("refs/heads/", "")
        branch = self.get_branch(owner, name, branch_name)
        if branch:
            return GitHubRef(ref=f"refs/heads/{branch_name}", sha=branch.sha)
        return None

    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool:
        if self.simulate_permission_denied:
            raise GitHubAPIError("Resource not accessible by integration (permission denied)", status_code=403)
        key = self._get_key(owner, name)
        self.get_repo(owner, name)

        if key not in self.branches:
            self.branches[key] = {base_branch: "0000000000000000000000000000000000000001"}

        base_sha = self.branches[key].get(base_branch, "0000000000000000000000000000000000000001")
        self.branches[key][new_branch] = base_sha
        logger.info(f"[MockGitHub] Created branch '{new_branch}' from '{base_branch}' in {key}")
        return True

    def create_blob(self, owner: str, name: str, content: str) -> str:
        blob_sha = f"blob-{hash(content) & 0xffffffff:08x}"
        return blob_sha

    def create_tree(self, owner: str, name: str, base_tree_sha: str, files: List[Dict[str, Any]]) -> str:
        return f"tree-{len(files)}-{int(time.time())}"

    def create_commit(
        self,
        owner: str,
        name: str,
        message: str,
        tree_sha: str,
        parents: List[str],
    ) -> GitHubCommit:
        key = self._get_key(owner, name)
        self.commit_counter += 1
        commit_sha = f"c000{self.commit_counter}000000000000000000000000000"
        commit = GitHubCommit(
            sha=commit_sha,
            message=message,
            tree_sha=tree_sha,
            parents=parents,
            author="RepoPilot <repopilot@users.noreply.github.com>",
        )
        if key not in self.commits:
            self.commits[key] = {}
        self.commits[key][commit_sha] = commit
        return commit

    def update_ref(self, owner: str, name: str, ref: str, sha: str, force: bool = False) -> bool:
        key = self._get_key(owner, name)
        branch_name = ref.replace("heads/", "").replace("refs/heads/", "")
        if key not in self.branches:
            self.branches[key] = {}
        self.branches[key][branch_name] = sha
        return True

    def list_pull_requests(
        self,
        owner: str,
        name: str,
        state: str = "open",
        head: Optional[str] = None,
        base: Optional[str] = None,
    ) -> List[GitHubPullRequest]:
        key = self._get_key(owner, name)
        prs = self.pull_requests.get(key, [])
        filtered = []
        for pr in prs:
            if state != "all" and pr.state != state:
                continue
            if head and pr.head_branch != head and not pr.head_branch.endswith(f":{head}"):
                continue
            if base and pr.base_branch != base:
                continue
            filtered.append(pr)
        return filtered

    def get_pull_request(self, owner: str, name: str, pr_number: int) -> Optional[GitHubPullRequest]:
        key = self._get_key(owner, name)
        for pr in self.pull_requests.get(key, []):
            if pr.number == pr_number:
                return pr
        return None

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
        if self.simulate_permission_denied:
            raise GitHubAPIError("Resource not accessible by integration (permission denied)", status_code=403)
        if self.simulate_rate_limit:
            raise GitHubAPIError("API rate limit exceeded", status_code=403)

        key = self._get_key(owner, name)
        self.pr_counter += 1
        mergeable = False if self.simulate_merge_conflict else True
        mergeable_state = "dirty" if self.simulate_merge_conflict else "clean"

        head_sha = self.branches.get(key, {}).get(head_branch, "head-sha-mock")

        pr = GitHubPullRequest(
            number=self.pr_counter,
            html_url=f"https://github.com/{owner}/{name}/pull/{self.pr_counter}",
            title=title,
            body=body,
            head_branch=head_branch,
            base_branch=base_branch,
            draft=draft,
            state="open",
            head_sha=head_sha,
            mergeable=mergeable,
            mergeable_state=mergeable_state,
        )
        if key not in self.pull_requests:
            self.pull_requests[key] = []
        self.pull_requests[key].append(pr)
        logger.info(f"[MockGitHub] Created PR #{pr.number}: {pr.title} ({pr.html_url})")
        return pr

    def update_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
    ) -> GitHubPullRequest:
        pr = self.get_pull_request(owner, name, pr_number)
        if not pr:
            raise GitHubAPIError(f"PR #{pr_number} not found", status_code=404)
        if title is not None:
            pr.title = title
        if body is not None:
            pr.body = body
        if state is not None:
            pr.state = state
        return pr

    def list_check_runs(self, owner: str, name: str, ref: str) -> List[GitHubCheckRun]:
        key = f"{owner}/{name}:{ref}"
        return self.check_runs.get(key, [
            GitHubCheckRun(name="unit-tests", status="completed", conclusion="success"),
            GitHubCheckRun(name="linter", status="completed", conclusion="success"),
        ])

    def list_reviews(self, owner: str, name: str, pr_number: int) -> List[GitHubReview]:
        key = f"{owner}/{name}:{pr_number}"
        return self.reviews.get(key, [])

    def merge_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        commit_title: str,
    ) -> bool:
        if self.simulate_permission_denied:
            raise GitHubAPIError("Cannot merge: write permissions required", status_code=403)
        if self.simulate_merge_conflict:
            raise GitHubAPIError("Cannot merge: pull request has conflicts", status_code=405)

        key = self._get_key(owner, name)
        prs = self.pull_requests.get(key, [])
        for pr in prs:
            if pr.number == pr_number:
                pr.merged = True
                pr.state = "closed"
                logger.info(f"[MockGitHub] Merged PR #{pr_number} in {key}")
                return True
        return False

    def add_labels(self, owner: str, name: str, issue_or_pr_number: int, labels: List[str]) -> List[str]:
        key = f"{owner}/{name}:{issue_or_pr_number}"
        if key not in self.labels:
            self.labels[key] = []
        for l in labels:
            if l not in self.labels[key]:
                self.labels[key].append(l)
        return self.labels[key]

    def check_rate_limit(self) -> Dict[str, Any]:
        return {
            "limit": 5000,
            "remaining": 4999,
            "reset": int(time.time()) + 3600,
        }


class RestGitHubClient(GitHubClientInterface):
    """Production GitHub REST API client using urllib, featuring rate-limit handling and bounded retries."""

    def __init__(
        self,
        token: Optional[str] = None,
        credentials: Optional[GitHubCredentials] = None,
        max_retries: int = 3,
        base_backoff_seconds: float = 0.5,
    ):
        if credentials:
            self.credentials = credentials
        elif token:
            self.credentials = GitHubCredentials(token=token)
        else:
            self.credentials = GitHubCredentials.from_environment()

        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds

        if not self.credentials.has_token:
            logger.warning(
                "No GitHub token configured. RestGitHubClient will operate with anonymous rate limits (60 requests/hr)."
            )

    @property
    def token(self) -> Optional[str]:
        """Backward-compatible property for existing code accessing client.token."""
        return self.credentials._token

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        custom_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{GITHUB_API_BASE}{endpoint}" if not endpoint.startswith("http") else endpoint
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoPilot-Autonomous-Agent/0.1.0",
        }
        if self.credentials.has_token:
            headers.update(self.credentials.get_auth_header())
        if custom_headers:
            headers.update(custom_headers)

        body_bytes = None
        if data is not None:
            body_bytes = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempt = 0
        while attempt <= self.max_retries:
            attempt += 1
            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 204:
                        return None
                    resp_text = resp.read().decode("utf-8")
                    return json.loads(resp_text) if resp_text else {}

            except urllib.error.HTTPError as e:
                # Capture and sanitize response
                msg = e.read().decode("utf-8", errors="ignore")

                # 1. Rate limiting check (HTTP 403 or 429)
                rate_remaining = e.headers.get("x-ratelimit-remaining")
                retry_after = e.headers.get("retry-after")

                if e.code in (403, 429) and (rate_remaining == "0" or retry_after or "rate limit" in msg.lower()):
                    sleep_time = float(retry_after) if retry_after else (2.0 ** attempt) * self.base_backoff_seconds
                    if attempt <= self.max_retries and sleep_time <= 10.0:
                        logger.warning(f"Rate limited by GitHub. Backing off for {sleep_time:.1f}s before retry {attempt}...")
                        time.sleep(sleep_time)
                        continue
                    raise GitHubAPIError(
                        "GitHub API Rate Limit Exceeded.",
                        status_code=e.code,
                        details={"message": msg, "retry_after": retry_after},
                    ) from e

                # 2. Authentication failure (HTTP 401)
                if e.code == 401:
                    logger.error("GitHub API 401 Unauthorized: Invalid, expired, or missing credentials.")
                    raise GitHubAPIError("GitHub authentication failed: Bad credentials or expired token", status_code=401) from e

                # 3. Permanent permission failure (HTTP 403 without rate limit)
                if e.code == 403:
                    logger.error(f"GitHub API 403 Forbidden: Insufficient permissions for {method} {endpoint}.")
                    raise GitHubAPIError(f"GitHub Permission Denied: {e.reason}", status_code=403, details={"response": msg}) from e

                # 4. Transient 5xx server errors - eligible for bounded retry
                if e.code in (500, 502, 503, 504) and attempt <= self.max_retries:
                    sleep_time = (2.0 ** attempt) * self.base_backoff_seconds
                    logger.warning(f"GitHub temporary server error {e.code}. Retrying in {sleep_time:.1f}s (attempt {attempt})...")
                    time.sleep(sleep_time)
                    continue

                # Permanent failure or retries exhausted
                logger.error(f"GitHub API Error {e.code} on {method} {endpoint}: {msg}")
                raise GitHubAPIError(f"GitHub API Error {e.code}: {e.reason}", status_code=e.code, details={"response": msg}) from e

            except urllib.error.URLError as e:
                # Network connectivity or socket timeout
                if attempt <= self.max_retries:
                    sleep_time = (2.0 ** attempt) * self.base_backoff_seconds
                    logger.warning(f"Network error: {e.reason}. Retrying in {sleep_time:.1f}s (attempt {attempt})...")
                    time.sleep(sleep_time)
                    continue
                logger.error(f"GitHub API Network Error after {self.max_retries} retries: {e}")
                raise GitHubAPIError(f"GitHub Network Error: {e.reason}") from e

        raise GitHubAPIError(f"Retry limit exhausted ({self.max_retries} attempts) for {method} {endpoint}")

    def get_repo(self, owner: str, name: str) -> GitHubRepoInfo:
        data = self._request(f"/repos/{owner}/{name}")
        return GitHubRepoInfo(
            owner=owner,
            name=name,
            default_branch=data.get("default_branch", "main"),
            is_private=data.get("private", False),
            description=data.get("description"),
            permissions=data.get("permissions", {}),
        )

    def list_open_issues(self, owner: str, name: str) -> List[GitHubIssue]:
        data = self._request(f"/repos/{owner}/{name}/issues?state=open&per_page=50")
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

    def get_branch(self, owner: str, name: str, branch: str) -> Optional[GitHubBranch]:
        try:
            data = self._request(f"/repos/{owner}/{name}/branches/{branch}")
            return GitHubBranch(
                name=data["name"],
                sha=data["commit"]["sha"],
                protected=data.get("protected", False),
            )
        except GitHubAPIError as e:
            if e.status_code == 404:
                return None
            raise

    def get_ref(self, owner: str, name: str, ref: str) -> Optional[GitHubRef]:
        clean_ref = ref.lstrip("/")
        if not clean_ref.startswith("refs/"):
            clean_ref = f"refs/heads/{clean_ref}"
        try:
            data = self._request(f"/repos/{owner}/{name}/git/ref/{clean_ref.replace('refs/', '')}")
            return GitHubRef(ref=data["ref"], sha=data["object"]["sha"])
        except GitHubAPIError as e:
            if e.status_code == 404:
                return None
            raise

    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool:
        ref_data = self._request(f"/repos/{owner}/{name}/git/ref/heads/{base_branch}")
        sha = ref_data["object"]["sha"]
        payload = {"ref": f"refs/heads/{new_branch}", "sha": sha}
        self._request(f"/repos/{owner}/{name}/git/refs", method="POST", data=payload)
        return True

    def create_blob(self, owner: str, name: str, content: str) -> str:
        payload = {"content": content, "encoding": "utf-8"}
        data = self._request(f"/repos/{owner}/{name}/git/blobs", method="POST", data=payload)
        return data["sha"]

    def create_tree(self, owner: str, name: str, base_tree_sha: str, files: List[Dict[str, Any]]) -> str:
        payload = {"base_tree": base_tree_sha, "tree": files}
        data = self._request(f"/repos/{owner}/{name}/git/trees", method="POST", data=payload)
        return data["sha"]

    def create_commit(
        self,
        owner: str,
        name: str,
        message: str,
        tree_sha: str,
        parents: List[str],
    ) -> GitHubCommit:
        payload = {
            "message": message,
            "tree": tree_sha,
            "parents": parents,
        }
        data = self._request(f"/repos/{owner}/{name}/git/commits", method="POST", data=payload)
        return GitHubCommit(
            sha=data["sha"],
            message=data["message"],
            author=data.get("author", {}).get("name", "RepoPilot"),
            tree_sha=tree_sha,
            parents=parents,
        )

    def update_ref(self, owner: str, name: str, ref: str, sha: str, force: bool = False) -> bool:
        clean_ref = ref.replace("refs/", "").replace("heads/", "")
        payload = {"sha": sha, "force": force}
        self._request(f"/repos/{owner}/{name}/git/refs/heads/{clean_ref}", method="PATCH", data=payload)
        return True

    def list_pull_requests(
        self,
        owner: str,
        name: str,
        state: str = "open",
        head: Optional[str] = None,
        base: Optional[str] = None,
    ) -> List[GitHubPullRequest]:
        query_parts = [f"state={state}", "per_page=50"]
        if head:
            query_parts.append(f"head={head}")
        if base:
            query_parts.append(f"base={base}")
        endpoint = f"/repos/{owner}/{name}/pulls?{'&'.join(query_parts)}"
        data = self._request(endpoint)
        prs = []
        for d in data:
            prs.append(
                GitHubPullRequest(
                    number=d["number"],
                    html_url=d["html_url"],
                    title=d["title"],
                    head_branch=d["head"]["ref"],
                    base_branch=d["base"]["ref"],
                    state=d.get("state", "open"),
                    draft=d.get("draft", False),
                    merged=d.get("merged", False),
                    body=d.get("body") or "",
                    head_sha=d["head"]["sha"],
                    mergeable=d.get("mergeable"),
                    mergeable_state=d.get("mergeable_state"),
                )
            )
        return prs

    def get_pull_request(self, owner: str, name: str, pr_number: int) -> Optional[GitHubPullRequest]:
        try:
            d = self._request(f"/repos/{owner}/{name}/pulls/{pr_number}")
            return GitHubPullRequest(
                number=d["number"],
                html_url=d["html_url"],
                title=d["title"],
                head_branch=d["head"]["ref"],
                base_branch=d["base"]["ref"],
                state=d.get("state", "open"),
                draft=d.get("draft", False),
                merged=d.get("merged", False),
                body=d.get("body") or "",
                head_sha=d["head"]["sha"],
                mergeable=d.get("mergeable"),
                mergeable_state=d.get("mergeable_state"),
            )
        except GitHubAPIError as e:
            if e.status_code == 404:
                return None
            raise

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
            body=data.get("body") or "",
            head_sha=data.get("head", {}).get("sha"),
        )

    def update_pull_request(
        self,
        owner: str,
        name: str,
        pr_number: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
    ) -> GitHubPullRequest:
        payload: Dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state

        data = self._request(f"/repos/{owner}/{name}/pulls/{pr_number}", method="PATCH", data=payload)
        return GitHubPullRequest(
            number=data["number"],
            html_url=data["html_url"],
            title=data["title"],
            head_branch=data["head"]["ref"],
            base_branch=data["base"]["ref"],
            state=data.get("state", "open"),
            draft=data.get("draft", False),
            body=data.get("body") or "",
            head_sha=data.get("head", {}).get("sha"),
        )

    def list_check_runs(self, owner: str, name: str, ref: str) -> List[GitHubCheckRun]:
        data = self._request(f"/repos/{owner}/{name}/commits/{ref}/check-runs")
        check_runs = []
        for item in data.get("check_runs", []):
            check_runs.append(
                GitHubCheckRun(
                    name=item["name"],
                    status=item.get("status", "completed"),
                    conclusion=item.get("conclusion"),
                    html_url=item.get("html_url"),
                )
            )
        return check_runs

    def list_reviews(self, owner: str, name: str, pr_number: int) -> List[GitHubReview]:
        data = self._request(f"/repos/{owner}/{name}/pulls/{pr_number}/reviews")
        reviews = []
        for item in data:
            reviews.append(
                GitHubReview(
                    id=item["id"],
                    user=item.get("user", {}).get("login", "unknown"),
                    state=item.get("state", "PENDING"),
                )
            )
        return reviews

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

    def add_labels(self, owner: str, name: str, issue_or_pr_number: int, labels: List[str]) -> List[str]:
        try:
            payload = {"labels": labels}
            data = self._request(
                f"/repos/{owner}/{name}/issues/{issue_or_pr_number}/labels",
                method="POST",
                data=payload,
            )
            return [l["name"] for l in data]
        except GitHubAPIError as e:
            logger.warning(f"Could not apply labels {labels} to PR #{issue_or_pr_number}: {e}")
            return []

    def check_rate_limit(self) -> Dict[str, Any]:
        data = self._request("/rate_limit")
        core = data.get("resources", {}).get("core", {})
        return {
            "limit": core.get("limit", 60),
            "remaining": core.get("remaining", 60),
            "reset": core.get("reset", int(time.time())),
        }
