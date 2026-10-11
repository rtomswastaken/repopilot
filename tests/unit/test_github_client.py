"""Comprehensive unit tests for GitHub operations and publishing subsystem.

Covers all 22 mandatory testing requirements:
1. Branch creation.
2. Existing branch detection.
3. Branch-name sanitisation.
4. Commit creation.
5. Empty-commit rejection.
6. Unexpected diff rejection.
7. Missing validation results.
8. Failed validation blocking publication.
9. Pull-request creation.
10. Existing pull-request detection.
11. Duplicate-task prevention.
12. Base-branch changes.
13. Merge eligibility.
14. Automatic merging disabled.
15. Required checks failing.
16. Human approval required.
17. Security-sensitive changes.
18. Insufficient permissions.
19. API rate limiting.
20. Authentication failures.
21. Merge conflicts.
22. Retry exhaustion.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import urllib.error
import urllib.response
import pytest

from repopilot.config.models import RepositoryConfig, ValidationSettings
from repopilot.core.exceptions import GitHubAPIError
from repopilot.core.models import (
    ChangeAction,
    ChangePlan,
    FileChange,
    PublishMode,
    Task,
    TaskCategory,
    TaskStatus,
    ValidationCheck,
    ValidationResult,
)
from repopilot.github.auth import (
    AuthMethod,
    GitHubCredentials,
    OPERATION_PERMISSIONS,
    redact_token,
)
from repopilot.github.branch_manager import BranchManager
from repopilot.github.client import MockGitHubClient, RestGitHubClient
from repopilot.github.commit_builder import CommitBuilder
from repopilot.github.conflicts import ConflictDetector
from repopilot.github.merge_engine import MergePolicyEngine
from repopilot.github.models import (
    GitHubCheckRun,
    GitHubPullRequest,
    GitHubReview,
    PublishStatus,
)
from repopilot.github.pr_manager import PullRequestManager
from repopilot.github.publisher import GitHubPublisher
from repopilot.github.security import SecurityPolicyChecker


# Helper fixtures
def create_sample_repo_config(auto_merge: bool = False) -> RepositoryConfig:
    return RepositoryConfig(
        owner="test-owner",
        name="test-repo",
        enabled=True,
        base_branch="main",
        auto_merge=auto_merge,
        allowed_tasks=[TaskCategory.BUG_FIX, TaskCategory.DOCUMENTATION, TaskCategory.TESTS],
        excluded_paths=[".github/workflows/", "secrets/", ".env"],
    )


def create_sample_task(task_id: str = "task-101", category: TaskCategory = TaskCategory.BUG_FIX) -> Task:
    return Task(
        id=task_id,
        repo_owner="test-owner",
        repo_name="test-repo",
        category=category,
        title="Fix null pointer exception in parser",
        description="Resolved edge case where empty input string causes crash.",
    )


def create_sample_plan(task_id: str = "task-101", path: str = "src/parser.py") -> ChangePlan:
    return ChangePlan(
        task_id=task_id,
        title="Fix null pointer",
        description="Handle empty input",
        files=[
            FileChange(
                path=path,
                action=ChangeAction.MODIFY,
                content="def parse(text):\n    if not text: return None\n    return text.strip()\n",
                diff="@@ -1,2 +1,3 @@\n+    if not text: return None",
                rationale="Guard clause against empty string",
            )
        ],
    )


def create_sample_validation(task_id: str = "task-101", passed: bool = True) -> ValidationResult:
    return ValidationResult(
        task_id=task_id,
        passed=passed,
        checks=[
            ValidationCheck(
                name="pytest",
                passed=passed,
                exit_code=0 if passed else 1,
                stdout="1 passed" if passed else "1 failed",
                duration_seconds=1.2,
            )
        ],
    )


# -----------------------------------------------------------------------------
# 1. Branch creation
# -----------------------------------------------------------------------------
def test_branch_creation():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    res = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res.status == PublishStatus.PUBLISHED
    assert res.branch_name is not None
    assert res.branch_name.startswith("repopilot/bug_fix/")
    assert client.get_branch(repo_cfg.owner, repo_cfg.name, res.branch_name) is not None


# -----------------------------------------------------------------------------
# 2. Existing branch detection
# -----------------------------------------------------------------------------
def test_existing_branch_detection():
    client = MockGitHubClient()
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    publisher = GitHubPublisher(client)
    # First publication creates branch
    res1 = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res1.status == PublishStatus.PUBLISHED

    # Second publication detects and safely reuses the RepoPilot-owned branch
    res2 = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res2.status in (PublishStatus.UPDATED, PublishStatus.PUBLISHED)
    assert res2.branch_name == res1.branch_name


# -----------------------------------------------------------------------------
# 3. Branch-name sanitisation
# -----------------------------------------------------------------------------
def test_branch_name_sanitisation():
    # Test illegal characters, spaces, and consecutive slashes/dots
    raw = "repopilot/feature: user~1/fix..now? *test* [dev]\\@{lock}.lock"
    sanitized = BranchManager.sanitize_branch_name(raw)

    assert not any(c in sanitized for c in [" ", "~", "^", ":", "?", "*", "[", "]", "\\", "@", "{", "}"])
    assert ".." not in sanitized
    assert "//" not in sanitized
    assert not sanitized.endswith(".lock")
    assert BranchManager.is_valid_branch_name(sanitized)

    # Generated branch name conforms strictly to conventions
    generated = BranchManager.generate_branch_name("task 999", "Fix Bug / Critical!", category="bug_fix")
    assert BranchManager.is_valid_branch_name(generated)
    assert generated.startswith("repopilot/bug_fix/task999-fix-bug-critical")


# -----------------------------------------------------------------------------
# 4. Commit creation
# -----------------------------------------------------------------------------
def test_commit_creation():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    res = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res.commit_sha is not None
    assert res.commit_sha in client.commits[f"{repo_cfg.owner}/{repo_cfg.name}"]

    commit = client.commits[f"{repo_cfg.owner}/{repo_cfg.name}"][res.commit_sha]
    assert "[bug_fix] Fix null pointer" in commit.message
    assert "src/parser.py" in commit.message


# -----------------------------------------------------------------------------
# 5. Empty-commit rejection
# -----------------------------------------------------------------------------
def test_empty_commit_rejection():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    val = create_sample_validation()

    # Plan with no files
    empty_plan = ChangePlan(task_id=task.id, title="Empty", description="No files", files=[])
    res = publisher.publish_validated_change(repo_cfg, task, empty_plan, val)
    assert res.status == PublishStatus.REJECTED
    assert "Empty commit rejected" in (res.error_message or "")

    # Plan with blank file content
    blank_file_plan = ChangePlan(
        task_id=task.id,
        title="Blank",
        description="Empty content",
        files=[FileChange(path="src/parser.py", action=ChangeAction.MODIFY, content="", diff="")],
    )
    res2 = publisher.publish_validated_change(repo_cfg, task, blank_file_plan, val)
    assert res2.status == PublishStatus.REJECTED
    assert "Empty commit rejected" in (res2.error_message or "")


# -----------------------------------------------------------------------------
# 6. Unexpected diff rejection
# -----------------------------------------------------------------------------
def test_unexpected_diff_rejection():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    val = create_sample_validation()

    # Prohibited path modification (.github/workflows/ci.yml)
    forbidden_plan = create_sample_plan(path=".github/workflows/ci.yml")
    res = publisher.publish_validated_change(repo_cfg, task, forbidden_plan, val)
    assert res.status == PublishStatus.REJECTED
    assert "excluded path rule" in (res.error_message or "")


# -----------------------------------------------------------------------------
# 7. Missing validation results
# -----------------------------------------------------------------------------
def test_missing_validation_results():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()

    # Attempt to publish without any validation result
    res = publisher.publish_validated_change(repo_cfg, task, plan, validation=None)
    assert res.status == PublishStatus.REJECTED
    assert "Missing validation result" in (res.error_message or "")


# -----------------------------------------------------------------------------
# 8. Failed validation blocking publication
# -----------------------------------------------------------------------------
def test_failed_validation_blocking_publication():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    failed_val = create_sample_validation(passed=False)

    res = publisher.publish_validated_change(repo_cfg, task, plan, failed_val)
    assert res.status == PublishStatus.REJECTED
    assert "Failed validation blocked publication" in (res.error_message or "")
    # Verify no PR was opened
    assert len(client.pull_requests.get(f"{repo_cfg.owner}/{repo_cfg.name}", [])) == 0


# -----------------------------------------------------------------------------
# 9. Pull-request creation
# -----------------------------------------------------------------------------
def test_pull_request_creation():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    res = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res.status == PublishStatus.PUBLISHED
    assert res.pr_number is not None
    assert res.pr_url is not None

    pr = client.get_pull_request(repo_cfg.owner, repo_cfg.name, res.pr_number)
    assert pr is not None
    assert pr.title == f"[bug_fix] {task.title}"
    assert "src/parser.py" in pr.body
    assert "✅ All automated tests and linters passed." in pr.body
    assert f"repopilot:task_id={task.id}" in pr.body


# -----------------------------------------------------------------------------
# 10. Existing pull-request detection
# -----------------------------------------------------------------------------
def test_existing_pull_request_detection():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    # Create initial PR
    res1 = publisher.publish_validated_change(repo_cfg, task, plan, val)
    initial_pr_number = res1.pr_number

    # Subsequent publication detects existing PR and updates it
    plan.files[0].content = "# updated content\n"
    res2 = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res2.status == PublishStatus.UPDATED
    assert res2.pr_number == initial_pr_number

    # Ensure no duplicate PR created
    prs = client.list_pull_requests(repo_cfg.owner, repo_cfg.name)
    assert len(prs) == 1


# -----------------------------------------------------------------------------
# 11. Duplicate-task prevention
# -----------------------------------------------------------------------------
def test_duplicate_task_prevention():
    client = MockGitHubClient()
    repo_cfg = create_sample_repo_config()
    task = create_sample_task(task_id="task-dup-1")
    plan = create_sample_plan(task_id="task-dup-1")
    val = create_sample_validation(task_id="task-dup-1")

    publisher = GitHubPublisher(client)
    res1 = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res1.status == PublishStatus.PUBLISHED

    # Searching existing PRs returns the one already created for task-dup-1
    open_prs = client.list_pull_requests(repo_cfg.owner, repo_cfg.name)
    detected_pr = PullRequestManager.find_existing_task_pr(open_prs, "task-dup-1", res1.branch_name)
    assert detected_pr is not None
    assert detected_pr.number == res1.pr_number


# -----------------------------------------------------------------------------
# 12. Base-branch changes
# -----------------------------------------------------------------------------
def test_base_branch_changes():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    # Simulate base branch HEAD moved after our task started
    old_base_sha = "0000000000000000000000000000000000000001"
    new_base_sha = "ffffffffffffffffffffffffffffffffffffffff"
    client.branches[f"{repo_cfg.owner}/{repo_cfg.name}"] = {
        "main": new_base_sha,
    }

    # If overlapping files were modified on base branch
    conflict = ConflictDetector.analyze_base_drift(
        repository=f"{repo_cfg.owner}/{repo_cfg.name}",
        branch="main",
        starting_base_sha=old_base_sha,
        current_base_sha=new_base_sha,
        changed_files=["src/parser.py"],
        base_modified_files=["src/parser.py"],
    )
    assert conflict is not None
    assert conflict.recoverable is False
    assert "Unrecoverable file overlap" in conflict.reason


# -----------------------------------------------------------------------------
# 13. Merge eligibility (all 10 conditions satisfied)
# -----------------------------------------------------------------------------
def test_merge_eligibility_all_conditions_satisfied():
    client = MockGitHubClient()
    repo_cfg = create_sample_repo_config(auto_merge=True)
    task = create_sample_task(category=TaskCategory.DOCUMENTATION)
    plan = create_sample_plan(path="README.md")
    val = create_sample_validation()
    task.validation = val
    task.plan = plan

    publisher = GitHubPublisher(client)
    res = publisher.publish_validated_change(repo_cfg, task, plan, val)

    assert res.status == PublishStatus.PUBLISHED
    assert res.merged is True
    assert res.merge_result is not None
    assert res.merge_result.eligible is True
    assert len(res.merge_result.missing_conditions) == 0


# -----------------------------------------------------------------------------
# 14. Automatic merging disabled
# -----------------------------------------------------------------------------
def test_automatic_merging_disabled():
    client = MockGitHubClient()
    repo_cfg = create_sample_repo_config(auto_merge=False)
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    publisher = GitHubPublisher(client)
    res = publisher.publish_validated_change(repo_cfg, task, plan, val)

    assert res.status == PublishStatus.PUBLISHED
    assert res.merged is False
    pr = client.get_pull_request(repo_cfg.owner, repo_cfg.name, res.pr_number)
    assert pr.merged is False
    assert pr.state == "open"


# -----------------------------------------------------------------------------
# 15. Required checks failing
# -----------------------------------------------------------------------------
def test_required_checks_failing():
    repo_cfg = create_sample_repo_config(auto_merge=True)
    task = create_sample_task(category=TaskCategory.DOCUMENTATION)
    plan = create_sample_plan(path="docs/guide.md")
    val = create_sample_validation()
    task.validation = val
    task.plan = plan

    pr = GitHubPullRequest(
        number=101,
        html_url="https://github.com/test-owner/test-repo/pull/101",
        title="Docs update",
        head_branch="repopilot/task-101",
        base_branch="main",
        mergeable=True,
        mergeable_state="clean",
    )

    failing_checks = [
        GitHubCheckRun(name="lint", status="completed", conclusion="success"),
        GitHubCheckRun(name="integration-tests", status="completed", conclusion="failure"),
    ]

    eligibility = MergePolicyEngine.evaluate_merge_eligibility(
        repo_cfg=repo_cfg,
        task=task,
        pr=pr,
        status_checks=failing_checks,
        repo_permissions={"push": True},
    )
    assert eligibility.eligible is False
    assert any("Required status checks failed" in cond for cond in eligibility.missing_conditions)


# -----------------------------------------------------------------------------
# 16. Human approval required
# -----------------------------------------------------------------------------
def test_human_approval_required():
    repo_cfg = create_sample_repo_config(auto_merge=True)
    task = create_sample_task(category=TaskCategory.DOCUMENTATION)
    plan = create_sample_plan(path="docs/guide.md")
    val = create_sample_validation()
    task.validation = val
    task.plan = plan

    pr = GitHubPullRequest(
        number=102,
        html_url="https://github.com/test-owner/test-repo/pull/102",
        title="Docs update",
        head_branch="repopilot/task-102",
        base_branch="main",
        mergeable=True,
        mergeable_state="clean",
    )

    # Review changes requested
    reviews_with_block = [
        GitHubReview(id=1, user="senior-dev", state="CHANGES_REQUESTED")
    ]

    eligibility = MergePolicyEngine.evaluate_merge_eligibility(
        repo_cfg=repo_cfg,
        task=task,
        pr=pr,
        status_checks=[],
        reviews=reviews_with_block,
        repo_permissions={"push": True},
    )
    assert eligibility.eligible is False
    assert any("Human reviewer requested changes" in cond for cond in eligibility.missing_conditions)

    # Mandatory review required when require_approval=True
    eligibility_no_reviews = MergePolicyEngine.evaluate_merge_eligibility(
        repo_cfg=repo_cfg,
        task=task,
        pr=pr,
        status_checks=[],
        reviews=[],
        repo_permissions={"push": True},
        require_approval=True,
    )
    assert eligibility_no_reviews.eligible is False
    assert any("Mandatory human code review approval required" in cond for cond in eligibility_no_reviews.missing_conditions)


# -----------------------------------------------------------------------------
# 17. Security-sensitive changes
# -----------------------------------------------------------------------------
def test_security_sensitive_changes():
    client = MockGitHubClient()
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config(auto_merge=True)
    task = create_sample_task()
    val = create_sample_validation()

    # 1. Hardcoded token in diff/content
    leaked_secret_plan = ChangePlan(
        task_id=task.id,
        title="Add auth client",
        description="Leaked token",
        files=[
            FileChange(
                path="src/client.py",
                action=ChangeAction.MODIFY,
                content='API_KEY = "ghp_123456789012345678901234567890123456"\n',
                rationale="Add api key",
            )
        ],
    )
    res_secret = publisher.publish_validated_change(repo_cfg, task, leaked_secret_plan, val)
    assert res_secret.status == PublishStatus.REJECTED
    assert "Potential secret detected" in (res_secret.error_message or "")

    # 2. Touching auth code blocks automated merging
    auth_plan = ChangePlan(
        task_id=task.id,
        title="Modify oauth",
        description="Auth update",
        files=[
            FileChange(
                path="src/auth/oauth_handler.py",
                action=ChangeAction.MODIFY,
                content="def login(): pass\n",
                rationale="Update oauth flow",
            )
        ],
    )
    task.plan = auth_plan
    task.validation = val
    pr = GitHubPullRequest(
        number=103,
        html_url="https://github.com/test-owner/test-repo/pull/103",
        title="Oauth update",
        head_branch="repopilot/task-103",
        base_branch="main",
        mergeable=True,
        mergeable_state="clean",
    )
    eligibility_auth = MergePolicyEngine.evaluate_merge_eligibility(
        repo_cfg=repo_cfg,
        task=task,
        pr=pr,
        status_checks=[],
        reviews=[],
        repo_permissions={"push": True},
    )
    assert eligibility_auth.eligible is False
    assert any("Touches security-sensitive areas" in cond for cond in eligibility_auth.missing_conditions)


# -----------------------------------------------------------------------------
# 18. Insufficient permissions
# -----------------------------------------------------------------------------
def test_insufficient_permissions():
    client = MockGitHubClient()
    client.simulate_permission_denied = True
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    res = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res.status == PublishStatus.REJECTED
    assert "permission denied" in (res.error_message or "").lower()


# -----------------------------------------------------------------------------
# 19. API rate limiting
# -----------------------------------------------------------------------------
def test_api_rate_limiting():
    client = MockGitHubClient()
    client.simulate_rate_limit = True
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config()
    task = create_sample_task()
    plan = create_sample_plan()
    val = create_sample_validation()

    res = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res.status == PublishStatus.REJECTED
    assert "rate limit" in (res.error_message or "").lower()


# -----------------------------------------------------------------------------
# 20. Authentication failures
# -----------------------------------------------------------------------------
def test_authentication_failures():
    # 1. Expired credentials
    expired_time = datetime.now(timezone.utc) - timedelta(hours=1)
    creds = GitHubCredentials(token="ghp_expiredtoken1234567890", expires_at=expired_time)
    assert creds.is_expired() is True
    with pytest.raises(GitHubAPIError) as exc_info:
        creds.get_raw_token()
    assert "expired" in str(exc_info.value).lower()

    # 2. Token redaction never leaks token
    masked = repr(creds)
    assert "ghp_expiredtoken1234567890" not in masked
    assert "ghp_***7890" in masked

    # 3. Client auth failure simulation
    client = MockGitHubClient()
    client.simulate_auth_failure = True
    with pytest.raises(GitHubAPIError) as exc:
        client.get_repo("owner", "repo")
    assert exc.value.status_code == 401


# -----------------------------------------------------------------------------
# 21. Merge conflicts
# -----------------------------------------------------------------------------
def test_merge_conflicts():
    client = MockGitHubClient()
    client.simulate_merge_conflict = True
    publisher = GitHubPublisher(client)
    repo_cfg = create_sample_repo_config(auto_merge=True)
    task = create_sample_task(category=TaskCategory.DOCUMENTATION)
    plan = create_sample_plan(path="README.md")
    val = create_sample_validation()

    res = publisher.publish_validated_change(repo_cfg, task, plan, val)
    assert res.status == PublishStatus.CONFLICT
    assert res.conflict_result is not None
    assert res.conflict_result.recoverable is False
    assert "merge conflicts" in res.conflict_result.reason.lower()


# -----------------------------------------------------------------------------
# 22. Retry exhaustion
# -----------------------------------------------------------------------------
def test_retry_exhaustion(monkeypatch):
    rest_client = RestGitHubClient(token="ghp_mocktesttoken1234567890", max_retries=2, base_backoff_seconds=0.01)

    call_count = 0

    def mock_urlopen_500(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(
            url="https://api.github.com/repos/test/repo",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=BytesIO(b'{"message": "Server error"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_500)

    with pytest.raises(GitHubAPIError) as exc_info:
        rest_client._request("/repos/test/repo")

    # Initial call + 2 retries = 3 calls
    assert call_count == 3
    assert exc_info.value.status_code == 500
