"""Unit tests for reporting and Markdown step summary generation."""

from datetime import datetime
from repopilot.core.models import (
    ExecutionReport,
    RepoExecutionSummary,
    TaskExecutionSummary,
    TaskStatus,
)
from repopilot.reporting.reporter import ExecutionReporter


def test_markdown_summary_generation():
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
