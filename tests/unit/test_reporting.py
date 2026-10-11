"""Unit tests for reporting, secret redaction, partial reports, duplicate prevention, and report accuracy."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import pytest

from repopilot.core.logging import redact_secrets
from repopilot.core.models import (
    ExecutionReport,
    RepoExecutionSummary,
    TaskExecutionSummary,
    TaskStatus,
)
from repopilot.github.client import MockGitHubClient
from repopilot.reporting.reporter import ExecutionReporter


def test_markdown_summary_generation():
    """Verify standard markdown summary rendering and ₹0.00 zero-cost display."""
    report = ExecutionReport(
        run_id="test-run-123",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        total_repositories=1,
        processed_repositories=1,
        repositories=[
            RepoExecutionSummary(
                repository="octocat/hello-world",
                tasks=[
                    TaskExecutionSummary(
                        task_id="t-1",
                        category="documentation",
                        title="Improve README",
                        status=TaskStatus.PUBLISHED,
                        pr_url="https://github.com/octocat/hello-world/pull/1",
                    )
                ],
                success_count=1,
                failure_count=0,
                skipped_count=0,
            )
        ],
    )

    md = ExecutionReporter.generate_markdown_summary(report)
    assert "# ✈️ RepoPilot Execution Summary" in md
    assert "test-run-123" in md
    assert "octocat/hello-world" in md
    assert "₹0.00" in md
    assert "https://github.com/octocat/hello-world/pull/1" in md


def test_secret_redaction():
    """Requirement 17: Scrub sensitive credentials and tokens from reports and diagnostics."""
    secret_pat = "ghp_1234567890abcdef1234567890abcdef"
    gemini_key = "AIzaSyFakeKeyWithExactLength33Chars"

    report = ExecutionReport(
        run_id="run-secret-check",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        total_repositories=1,
        processed_repositories=1,
        repositories=[
            RepoExecutionSummary(
                repository="org/private-repo",
                tasks=[
                    TaskExecutionSummary(
                        task_id="t-sec",
                        category="bug_fix",
                        title="Fix authentication flow",
                        status=TaskStatus.FAILED,
                        error=f"Authentication failed with token {secret_pat} and key {gemini_key}",
                    )
                ],
                success_count=0,
                failure_count=1,
            )
        ],
        errors=[f"Failed calling provider with {secret_pat}"],
    )

    # Markdown summary must be scrubbed
    md = ExecutionReporter.generate_markdown_summary(report)
    assert secret_pat not in md
    assert gemini_key not in md
    assert "[REDACTED_TOKEN]" in md

    # JSON report must be scrubbed
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_out = Path(tmp_dir) / "report.json"
        ExecutionReporter.write_json_report(report, json_out)
        content = json_out.read_text(encoding="utf-8")
        assert secret_pat not in content
        assert gemini_key not in content
        assert "[REDACTED_TOKEN]" in content


def test_partial_execution_reports():
    """Requirement 18: Render informative partial reports when runs are interrupted or halted early."""
    report = ExecutionReport(
        run_id="run-interrupted-early",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        total_repositories=3,
        processed_repositories=1,  # Halted after 1st repository due to timeout or budget
        repositories=[
            RepoExecutionSummary(
                repository="org/repo-one",
                tasks=[
                    TaskExecutionSummary(
                        task_id="t-done",
                        category="tests",
                        title="Add unit tests",
                        status=TaskStatus.PUBLISHED,
                        pr_url="https://github.com/org/repo-one/pull/5",
                    )
                ],
                success_count=1,
                failure_count=0,
            )
        ],
        errors=["Time budget exhausted after 10m. Execution stopped cleanly."],
    )

    md = ExecutionReporter.generate_markdown_summary(report, is_partial=True)
    assert "Partial Execution" in md
    assert "Time budget exhausted" in md
    assert "org/repo-one" in md
    assert "**Repositories Discovered:** 3" in md
    assert "**Repositories Processed:** 1" in md
    assert "**Repositories Skipped:** 2" in md

    with tempfile.TemporaryDirectory() as tmp_dir:
        json_out = Path(tmp_dir) / "partial.json"
        ExecutionReporter.write_json_report(report, json_out, is_partial=True)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert data["is_partial"] is True
        assert data["metrics"]["skipped_repositories"] == 2


def test_duplicate_report_prevention():
    """Requirement 19: Prevent creating duplicate GitHub issues on repeated runs."""
    from repopilot.github.models import GitHubIssue

    github = MockGitHubClient()
    owner = "org"
    repo = "my-repo"
    github.issues[f"{owner}/{repo}"] = []

    def mock_create_issue(o, n, title, body, labels=None):
        key = f"{o}/{n}"
        num = len(github.issues.get(key, [])) + 1
        issue = GitHubIssue(number=num, title=title, body=body, labels=labels or [])
        github.issues[key].append(issue)
        return issue

    github.create_issue = mock_create_issue

    report1 = ExecutionReport(
        run_id="run-1",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        total_repositories=1,
        processed_repositories=1,
        repositories=[
            RepoExecutionSummary(repository=f"{owner}/{repo}", success_count=1)
        ],
    )

    # Run 1: First report creates initial issue #1
    issue_id_1 = ExecutionReporter.publish_issue_report(report1, github, owner, repo)
    assert issue_id_1 == 1
    assert len(github.issues.get(f"{owner}/{repo}", [])) == 1

    # Run 2: Second report detects existing issue and updates/reuses it
    report2 = ExecutionReport(
        run_id="run-2",
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        total_repositories=1,
        processed_repositories=1,
        repositories=[
            RepoExecutionSummary(repository=f"{owner}/{repo}", success_count=2)
        ],
    )
    issue_id_2 = ExecutionReporter.publish_issue_report(report2, github, owner, repo)
    assert issue_id_2 == 1  # Reused existing issue
    # No duplicate issue was created!
    assert len(github.issues.get(f"{owner}/{repo}", [])) == 1


def test_report_accuracy():
    """Requirement 20: Ensure reported metrics correspond precisely to actual execution outcomes."""
    start = datetime.utcnow()
    end = start + timedelta(seconds=125)

    report = ExecutionReport(
        run_id="run-accuracy",
        started_at=start,
        completed_at=end,
        total_repositories=2,
        processed_repositories=2,
        total_ai_requests=7,
        repositories=[
            RepoExecutionSummary(
                repository="acme/api",
                tasks=[
                    TaskExecutionSummary(
                        task_id="t-1",
                        category="documentation",
                        title="Docs",
                        status=TaskStatus.PUBLISHED,
                        pr_url="https://github.com/acme/api/pull/10",
                    ),
                    TaskExecutionSummary(
                        task_id="t-2",
                        category="bug_fix",
                        title="Bug",
                        status=TaskStatus.FAILED,
                        error="Syntax error in patch",
                    ),
                ],
                success_count=1,
                failure_count=1,
                skipped_count=0,
            ),
            RepoExecutionSummary(
                repository="acme/ui",
                tasks=[
                    TaskExecutionSummary(
                        task_id="t-3",
                        category="code_quality",
                        title="Refactor",
                        status=TaskStatus.SKIPPED,
                    )
                ],
                success_count=0,
                failure_count=0,
                skipped_count=1,
            ),
        ],
    )

    metrics = ExecutionReporter.compute_summary_metrics(report)
    assert metrics["tasks_attempted"] == 3
    assert metrics["tasks_completed"] == 1
    assert metrics["tasks_failed"] == 1
    assert metrics["tasks_skipped"] == 1
    assert metrics["prs_created"] == 1
    assert metrics["duration_seconds"] == 125.0
    assert metrics["cost_inr"] == "₹0.00"

    md = ExecutionReporter.generate_markdown_summary(report)
    # Check that failed tasks are explicitly noted as failures
    assert "Syntax error in patch" in md
    # Check that PR url is included
    assert "https://github.com/acme/api/pull/10" in md
    # Check that zero-cost is explicitly shown
    assert "₹0.00" in md
