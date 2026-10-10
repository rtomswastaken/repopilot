"""Integration tests validating the complete end-to-end autonomous lifecycle."""

import tempfile
from pathlib import Path

from repopilot.ai.mock_provider import MockAIProvider
from repopilot.config.models import GlobalSettings, RepoPilotConfig, RepositoryConfig
from repopilot.core.models import PublishMode, TaskCategory, TaskStatus
from repopilot.github.client import MockGitHubClient
from repopilot.intelligence.analyzer import LocalRepositoryAnalyzer
from repopilot.orchestration.runner import Orchestrator
from repopilot.reporting.reporter import ExecutionReporter
from repopilot.state.store import LocalFileStateStore


def test_full_autonomous_lifecycle_e2e():
    """Simulate a complete scheduled run and subsequent run with duplicate avoidance."""
    with tempfile.TemporaryDirectory() as base_tmp:
        tmp_path = Path(base_tmp)
        state_file = tmp_path / "state.json"
        state_store = LocalFileStateStore(state_file)

        config = RepoPilotConfig(
            version=1,
            settings=GlobalSettings(
                max_repositories_per_run=1,
                max_tasks_per_repository=2,
                max_retries_per_task=1,
                max_execution_minutes=5,
                max_ai_requests_per_run=10,
                default_publish_mode=PublishMode.PULL_REQUEST,
            ),
            repositories=[
                RepositoryConfig(
                    owner="demo-owner",
                    name="demo-repo",
                    enabled=True,
                    goals=["improve_documentation"],
                    allowed_tasks=[TaskCategory.DOCUMENTATION],
                    publish_mode=PublishMode.PULL_REQUEST,
                    auto_merge=False,
                )
            ],
        )

        github_client = MockGitHubClient()
        ai_provider = MockAIProvider(available=True)
        analyzer = LocalRepositoryAnalyzer()

        # RUN 1: Fresh execution
        orchestrator1 = Orchestrator(
            config=config,
            github_client=github_client,
            ai_provider=ai_provider,
            state_store=state_store,
            analyzer=analyzer,
            workspace_base=tmp_path / "workspaces",
        )

        report1 = orchestrator1.run()

        # Verify outcomes from run 1
        assert report1.processed_repositories == 1
        assert len(report1.repositories) == 1
        repo_res = report1.repositories[0]
        assert repo_res.success_count >= 1
        assert repo_res.failure_count == 0

        # Check pull request was created in GitHub client
        prs = github_client.pull_requests.get("demo-owner/demo-repo", [])
        assert len(prs) >= 1
        assert "Autonomous Improvement" in prs[0].title or "README" in prs[0].title or "doc" in prs[0].title.lower()

        # Verify state persistence
        assert state_file.exists()
        persisted_state = state_store.load_state()
        assert "demo-owner/demo-repo" in persisted_state.repositories
        assert persisted_state.repositories["demo-owner/demo-repo"].total_tasks_completed >= 1

        # Verify Markdown summary generation
        md_summary = ExecutionReporter.generate_markdown_summary(report1)
        assert "RepoPilot Execution Summary" in md_summary
        assert "demo-owner/demo-repo" in md_summary
        assert "₹0.00" in md_summary

        # RUN 2: Subsequent run - should detect previously completed task as duplicate and skip!
        orchestrator2 = Orchestrator(
            config=config,
            github_client=github_client,
            ai_provider=ai_provider,
            state_store=state_store,
            analyzer=analyzer,
            workspace_base=tmp_path / "workspaces",
        )

        report2 = orchestrator2.run()
        repo_res2 = report2.repositories[0]
        # Should have skipped duplicate task
        assert repo_res2.skipped_count >= 1
        # No new PRs should have been created
        assert len(github_client.pull_requests.get("demo-owner/demo-repo", [])) == len(prs)
