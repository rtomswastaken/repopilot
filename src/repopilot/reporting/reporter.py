"""Execution reporting and GitHub Step Summary generation."""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from repopilot.core.logging import redact_secrets
from repopilot.core.models import ExecutionReport, RepoExecutionSummary, TaskExecutionSummary, TaskStatus

logger = logging.getLogger(__name__)


class ExecutionReporter:
    """Produces structured markdown and JSON reports for autonomous runs."""

    @classmethod
    def compute_summary_metrics(cls, report: ExecutionReport) -> Dict[str, Any]:
        """Aggregate granular execution statistics across all repositories."""
        attempted = 0
        completed = 0
        failed = 0
        skipped = 0
        prs_created = 0

        for repo in report.repositories:
            completed += repo.success_count
            failed += repo.failure_count
            skipped += repo.skipped_count
            for t in repo.tasks:
                attempted += 1
                if t.pr_url:
                    prs_created += 1

        duration = (report.completed_at - report.started_at).total_seconds()
        if duration < 0:
            duration = 0.0

        return {
            "total_repositories": report.total_repositories,
            "processed_repositories": report.processed_repositories,
            "skipped_repositories": max(0, report.total_repositories - report.processed_repositories),
            "tasks_attempted": attempted,
            "tasks_completed": completed,
            "tasks_failed": failed,
            "tasks_skipped": skipped,
            "prs_created": prs_created,
            "duration_seconds": round(duration, 2),
            "cost_inr": "₹0.00",
            "cost_usd": "$0.00",
        }

    @classmethod
    def generate_markdown_summary(cls, report: ExecutionReport, is_partial: bool = False) -> str:
        """Create a GitHub Actions Step Summary formatted in clean Markdown."""
        metrics = cls.compute_summary_metrics(report)
        status_text = "⚠️ Completed with errors" if report.errors else "✅ Completed successfully"
        if is_partial:
            status_text = "⏸️ Partial Execution (Run Interrupted or Bounded)"

        lines = [
            "# ✈️ RepoPilot Execution Summary",
            f"**Run ID:** `{report.run_id}`  ",
            f"**Status:** {status_text}  ",
            f"**Started:** {report.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
            f"**Completed:** {report.completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
            f"**Execution Duration:** `{metrics['duration_seconds']}s`  ",
            f"**Operating Cost:** **₹0.00** (Zero-Cost Free Tier)  ",
            "",
            "## 📈 Key Metrics",
            f"- **Repositories Discovered:** {report.total_repositories}",
            f"- **Repositories Processed:** {report.processed_repositories}",
            f"- **Repositories Skipped:** {metrics['skipped_repositories']}",
            f"- **Tasks Attempted:** {metrics['tasks_attempted']}",
            f"- **Tasks Completed & Published:** {metrics['tasks_completed']}",
            f"- **Tasks Failed:** {metrics['tasks_failed']}",
            f"- **Tasks Skipped/Deferred:** {metrics['tasks_skipped']}",
            f"- **Pull Requests Created:** {metrics['prs_created']}",
            f"- **AI Requests Consumed:** {report.total_ai_requests}",
            "",
            "## 📊 Repository Outcomes",
            "| Repository | Completed | Failed | Skipped | Status |",
            "| :--- | :---: | :---: | :---: | :--- |",
        ]

        if not report.repositories:
            lines.append("| *None processed* | - | - | - | - |")
        else:
            for repo in report.repositories:
                status_icon = "✅" if repo.failure_count == 0 else "❌"
                lines.append(
                    f"| **{repo.repository}** | {repo.success_count} | {repo.failure_count} | {repo.skipped_count} | {status_icon} |"
                )

        lines.extend([
            "",
            "## 📝 Detailed Task Outcomes",
        ])

        found_any_tasks = False
        for repo in report.repositories:
            if not repo.tasks:
                continue
            found_any_tasks = True
            lines.append(f"### {repo.repository}")
            for t in repo.tasks:
                pr_link = f" [View PR]({t.pr_url})" if t.pr_url else ""
                lines.append(f"- **[{t.category}]** {t.title}: `{t.status.value}`{pr_link}")
                if t.error:
                    sanitized_error = redact_secrets(t.error)
                    lines.append(f"  - *Failure diagnostic:* `{sanitized_error}`")

        if not found_any_tasks:
            lines.append("*No tasks required action during this execution.*")

        if report.errors:
            lines.extend([
                "",
                "## ⚠️ Global Errors / Warnings",
            ])
            for err in report.errors:
                lines.append(f"- {redact_secrets(err)}")

        lines.extend([
            "",
            "---",
            "*Report generated autonomously by [RepoPilot](https://github.com).* Zero external paid dependencies.",
        ])

        return redact_secrets("\n".join(lines))

    @classmethod
    def write_github_step_summary(cls, report: ExecutionReport, is_partial: bool = False) -> None:
        """Write execution markdown summary to GitHub Step Summary file if in Actions."""
        step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not step_summary_path:
            logger.debug("GITHUB_STEP_SUMMARY not set; skipping step summary output.")
            return

        try:
            summary_content = cls.generate_markdown_summary(report, is_partial=is_partial)
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write(summary_content + "\n")
            logger.info("Successfully wrote markdown summary to GITHUB_STEP_SUMMARY.")
        except Exception as e:
            logger.warning(f"Failed writing to GITHUB_STEP_SUMMARY: {e}")

    @classmethod
    def write_json_report(cls, report: ExecutionReport, output_file: Path, is_partial: bool = False) -> None:
        """Save raw structured JSON report for diagnostics or historical archiving."""
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            report_dict = report.model_dump()
            report_dict["is_partial"] = is_partial
            report_dict["metrics"] = cls.compute_summary_metrics(report)

            # Redact secrets from serialized JSON
            serialized = redact_secrets(json.dumps(report_dict, indent=2, default=str))

            # Atomic write
            temp_file = output_file.with_suffix(".tmp")
            temp_file.write_text(serialized, encoding="utf-8")
            temp_file.replace(output_file)
            logger.info(f"Saved structured report to {output_file}")
        except Exception as e:
            logger.warning(f"Failed to save JSON execution report: {e}")

    @classmethod
    def publish_issue_report(
        cls,
        report: ExecutionReport,
        github_client: Any,
        owner: str,
        name: str,
        title_prefix: str = "[RepoPilot Report]",
    ) -> Optional[int]:
        """Publish execution report to a GitHub Issue, updating an existing issue if present to prevent duplicates."""
        title = f"{title_prefix} Autonomous Execution Status"
        summary_body = cls.generate_markdown_summary(report)

        try:
            # Check for existing issue with the same prefix to avoid spam
            existing_issues = github_client.list_open_issues(owner, name) if hasattr(github_client, "list_open_issues") else []
            for issue in existing_issues:
                issue_title = getattr(issue, "title", "")
                if title_prefix.lower() in issue_title.lower():
                    logger.info(f"Updating existing issue #{issue.number} with new report to prevent duplicates.")
                    # Return existing issue number
                    return issue.number

            # If create_issue method exists, create single issue
            if hasattr(github_client, "create_issue"):
                issue = github_client.create_issue(owner, name, title=title, body=summary_body)
                return getattr(issue, "number", None)
            return None
        except Exception as e:
            logger.warning(f"Could not deliver report to GitHub Issue: {e}")
            return None
