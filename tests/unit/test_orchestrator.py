"""Unit tests for orchestrator coordination and multi-repository failure isolation."""

import tempfile
from pathlib import Path
from repopilot.ai.mock_provider import MockAIProvider
from repopilot.config.models import GlobalSettings, RepoPilotConfig, RepositoryConfig
from repopilot.core.models import TaskCategory, TaskStatus
from repopilot.github.client import MockGitHubClient
from repopilot.intelligence.analyzer import LocalRepositoryAnalyzer
from repopilot.orchestration.runner import Orchestrator
from repopilot.state.store import MockStateStore


def test_multi_repo_failure_isolation():
    """Verify that when processing fails in Repository A, Repository B continues normally."""
    config = RepoPilotConfig(
        version=1,
        settings=GlobalSettings(max_repositories_per_run=5, max_tasks_per_repository=1),
        repositories=[
            RepositoryConfig(
                owner="org-a",
                name="failing-repo",
                allowed_tasks=[TaskCategory.DOCUMENTATION],
            ),
            RepositoryConfig(
                owner="org-b",
                name="healthy-repo",
                allowed_tasks=[TaskCategory.DOCUMENTATION],
            ),
        ],
    )

    github = MockGitHubClient()
    # Inject failure only for repo A
    def broken_list_issues(owner, name):
        if name == "failing-repo":
            raise RuntimeError("GitHub API 500 Internal Server Error simulating failure")
        return []
    github.list_open_issues = broken_list_issues

    ai = MockAIProvider(available=True)
    state = MockStateStore()
    analyzer = LocalRepositoryAnalyzer()

    with tempfile.TemporaryDirectory() as tmp_dir:
        orchestrator = Orchestrator(
            config=config,
            github_client=github,
            ai_provider=ai,
            state_store=state,
            analyzer=analyzer,
            workspace_base=Path(tmp_dir),
        )
        report = orchestrator.run()

        # Both repositories should have been processed
        assert report.processed_repositories == 2
        assert len(report.repositories) == 2

        summary_a = report.repositories[0]
        summary_b = report.repositories[1]

        assert summary_a.repository == "org-a/failing-repo"
        assert summary_a.failure_count == 1

        assert summary_b.repository == "org-b/healthy-repo"
        assert summary_b.failure_count == 0
        assert summary_b.success_count == 1
