"""Execution reporting and GitHub Step Summary generation."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from repopilot.core.models import ExecutionReport

logger = logging.getLogger(__name__)


class ExecutionReporter:
    """Produces structured markdown and JSON reports for autonomous runs."""

    @staticmethod
    def generate_markdown_summary(report: ExecutionReport) -> str:
        """Create a GitHub Actions Step Summary formatted in Markdown."""
        lines = [
            f"# ✈️ RepoPilot Execution Summary",
            f"**Run ID:** `{report.run_id}`  ",
            f"**Status:** {'✅ Completed' if not report.errors else '⚠️ Completed with errors'}  ",
            f"**Started:** {report.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
            f"**Completed:** {report.completed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ",
            f"**Operating Cost:** **₹0.00** (Zero-Cost Free Tier)  ",
            "",
            "## 📊 Repository Metrics",
            "| Repository | Completed Tasks | Failed Tasks | Skipped | Status |",
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
                    lines.append(f"  - *Failure note:* `{t.error}`")

        if not found_any_tasks:
            lines.append("*No tasks required action during this execution.*")

        if report.errors:
            lines.extend([
                "",
                "## ⚠️ Global Errors / Warnings",
            ])
            for err in report.errors:
                lines.append(f"- {err}")

        lines.extend([
            "",
            "---",
            "*Report generated autonomously by [RepoPilot](https://github.com).* Zero external paid dependencies.",
        ])

        return "\n".join(lines)

    @classmethod
    def write_github_step_summary(cls, report: ExecutionReport) -> None:
        """Write execution markdown summary to GitHub Step Summary file if in Actions."""
        step_summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if not step_summary_path:
            logger.debug("GITHUB_STEP_SUMMARY not set; skipping step summary output.")
            return

        try:
            summary_content = cls.generate_markdown_summary(report)
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write(summary_content + "\n")
            logger.info("Successfully wrote markdown summary to GITHUB_STEP_SUMMARY.")
        except Exception as e:
            logger.warning(f"Failed writing to GITHUB_STEP_SUMMARY: {e}")

    @classmethod
    def write_json_report(cls, report: ExecutionReport, output_file: Path) -> None:
        """Save raw structured JSON report for diagnostics or historical archiving."""
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            logger.info(f"Saved structured report to {output_file}")
        except Exception as e:
            logger.warning(f"Failed to save JSON execution report: {e}")
