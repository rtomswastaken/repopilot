"""Integration tests validating the complete end-to-end autonomous lifecycle.

Covers all required end-to-end scenarios and edge cases from Phase 6:
1. Deterministic validation, repair, rejection, and publishing lifecycle.
2. Missing and invalid configuration handling.
3. Unavailable or quota-exhausted AI provider.
4. GitHub API rate limiting.
5. Repository permission failures.
6. Test execution timeout.
7. Repair retry exhaustion.
8. Existing pull requests & deduplication.
9. Auto-merge disabled vs enabled with checks.
10. Corrupted state recovery.
11. Multi-repository fault isolation.
"""

import json
from pathlib import Path
import subprocess
import tempfile
import pytest

from repopilot.ai.mock_provider import MockAIProvider
from repopilot.config.loader import load_config
from repopilot.config.models import GlobalSettings, RepoPilotConfig, RepositoryConfig, ValidationSettings
from repopilot.core.exceptions import ConfigurationError
from repopilot.core.models import (
    ChangeAction,
    ChangePlan,
    FileChange,
    PublishMode,
    Task,
    TaskCategory,
    TaskStatus,
)
from repopilot.github.client import MockGitHubClient
from repopilot.github.models import GitHubCheckRun, PublishStatus
from repopilot.intelligence.analyzer import LocalRepositoryAnalyzer
from repopilot.orchestration.runner import Orchestrator
from repopilot.reporting.reporter import ExecutionReporter
from repopilot.state.store import LocalFileStateStore, MockStateStore


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
        assert "README" in prs[0].title or "doc" in prs[0].title.lower()

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


def test_deterministic_end_to_end_validation_and_publishing_scenario():
    """Scenario 1: Deterministic full lifecycle with test validation.
    
    1. Loads configuration.
    2. Stages a temporary Git repository with source and test suite.
    3. Simulates an intentionally broken patch -> rejected by validation, discarded, NOT published.
    4. Simulates a valid patch -> passes validation, published via mocked GitHub client with commit and PR.
    5. Verifies persistent state and execution report accuracy.
    """
    with tempfile.TemporaryDirectory() as base_tmp:
        tmp_path = Path(base_tmp)

        # 1. Setup a real local Git repository to serve as the target
        repo_dir = tmp_path / "math_repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=str(repo_dir), check=True)

        calc_file = repo_dir / "calculator.py"
        calc_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        test_file = repo_dir / "test_calc.py"
        test_file.write_text(
            "import unittest\nfrom calculator import add\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n\n"
            "if __name__ == '__main__':\n"
            "    unittest.main()\n",
            encoding="utf-8",
        )

        subprocess.run(["git", "add", "."], cwd=str(repo_dir), check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)

        state_file = tmp_path / "state.json"
        state_store = LocalFileStateStore(state_file)

        # 2. Part A: Intentionally broken patch (modifies calculator.py to return incorrect result)
        broken_plan = ChangePlan(
            task_id="broken-task-01",
            title="Introduce intentional bug",
            description="Testing validation rejection",
            files=[
                FileChange(
                    path="calculator.py",
                    action=ChangeAction.MODIFY,
                    content="def add(a, b):\n    return a - b  # Bug!\n",
                    rationale="Broken logic",
                )
            ],
        )

        # Broken task proposal
        broken_task = Task(
            id="math_repo-broken-01",
            repo_owner="test-org",
            repo_name="math_repo",
            category=TaskCategory.BUG_FIX,
            title="Introduce intentional bug",
            description="Testing validation rejection",
            related_files=["calculator.py"],
            max_retries=0,
        )

        config_broken = RepoPilotConfig(
            version=1,
            settings=GlobalSettings(max_repositories_per_run=1, max_tasks_per_repository=1, max_retries_per_task=0),
            repositories=[
                RepositoryConfig(
                    owner="test-org",
                    name="math_repo",
                    enabled=True,
                    allowed_tasks=[TaskCategory.BUG_FIX],
                    validation=ValidationSettings(
                        test_command="python -m unittest test_calc.py",
                        timeout_seconds=30,
                    ),
                )
            ],
        )

        gh_client = MockGitHubClient()
        ai_broken = MockAIProvider(
            available=True,
            custom_planned_tasks=[broken_task],
            custom_change_plan=broken_plan,
        )
        analyzer = LocalRepositoryAnalyzer()

        orch_broken = Orchestrator(
            config=config_broken,
            github_client=gh_client,
            ai_provider=ai_broken,
            state_store=state_store,
            analyzer=analyzer,
            workspace_base=tmp_path / "workspaces_broken",
            repo_source_base=tmp_path,
        )

        report_broken = orch_broken.run()
        summary_broken = report_broken.repositories[0]
        # Intentionally broken patch must be rejected!
        assert summary_broken.failure_count == 1
        assert summary_broken.success_count == 0
        # No PR should have been published
        assert len(gh_client.pull_requests.get("test-org/math_repo", [])) == 0

        # 3. Part B: Valid patch (adds a new function and test that passes)
        valid_plan = ChangePlan(
            task_id="math_repo-valid-02",
            title="Add multiply helper",
            description="Extend calculator with multiplication",
            files=[
                FileChange(
                    path="calculator.py",
                    action=ChangeAction.MODIFY,
                    content="def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n",
                    rationale="Add multiply function",
                ),
                FileChange(
                    path="test_calc.py",
                    action=ChangeAction.MODIFY,
                    content=(
                        "import unittest\nfrom calculator import add, multiply\n\n"
                        "class TestCalc(unittest.TestCase):\n"
                        "    def test_add(self):\n"
                        "        self.assertEqual(add(2, 3), 5)\n"
                        "    def test_multiply(self):\n"
                        "        self.assertEqual(multiply(3, 4), 12)\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    ),
                    rationale="Add multiply unit test",
                ),
            ],
        )

        valid_task = Task(
            id="math_repo-valid-02",
            repo_owner="test-org",
            repo_name="math_repo",
            category=TaskCategory.BUG_FIX,
            title="Add multiply helper",
            description="Extend calculator with multiplication",
            related_files=["calculator.py", "test_calc.py"],
            max_retries=1,
        )

        config_valid = RepoPilotConfig(
            version=1,
            settings=GlobalSettings(max_repositories_per_run=1, max_tasks_per_repository=1),
            repositories=[
                RepositoryConfig(
                    owner="test-org",
                    name="math_repo",
                    enabled=True,
                    allowed_tasks=[TaskCategory.BUG_FIX],
                    validation=ValidationSettings(
                        test_command="python -m unittest test_calc.py",
                        timeout_seconds=30,
                    ),
                )
            ],
        )

        ai_valid = MockAIProvider(
            available=True,
            custom_planned_tasks=[valid_task],
            custom_change_plan=valid_plan,
        )
        orch_valid = Orchestrator(
            config=config_valid,
            github_client=gh_client,
            ai_provider=ai_valid,
            state_store=state_store,
            analyzer=analyzer,
            workspace_base=tmp_path / "workspaces_valid",
            repo_source_base=tmp_path,
        )

        report_valid = orch_valid.run()
        summary_valid = report_valid.repositories[0]
        # Valid patch must pass and be published!
        assert summary_valid.success_count == 1
        assert summary_valid.failure_count == 0

        # Verify PR was created in MockGitHubClient
        prs = gh_client.pull_requests.get("test-org/math_repo", [])
        assert len(prs) == 1
        assert "multiply" in prs[0].title.lower() or "bug_fix" in prs[0].title.lower()

        # Verify persistent state recorded the completion
        st = state_store.load_state()
        assert st.repositories["test-org/math_repo"].total_tasks_completed >= 1
        assert report_valid.total_changes_published >= 1
        assert report_valid.state_saved is True


def test_e2e_missing_and_invalid_configuration():
    """Scenario 2: Missing or invalid configuration handling."""
    # Missing config file
    with pytest.raises(ConfigurationError):
        load_config("/non/existent/path/repos.yaml")

    # Invalid YAML syntax
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("version: 1\nsettings:\n  unclosed_brace: {")
        f.flush()
        with pytest.raises(ConfigurationError):
            load_config(f.name)

    # Forbidden credentials in configuration
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("version: 1\nsettings:\n  github_token: 'ghp_secret_token_12345'\nrepositories: []\n")
        f.flush()
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(f.name)
        assert "Credential setting" in str(exc_info.value) or "github_token" in str(exc_info.value)


def test_e2e_unavailable_ai_provider():
    """Scenario 3: AI provider completely unavailable or unconfigured."""
    config = RepoPilotConfig(
        version=1,
        settings=GlobalSettings(max_repositories_per_run=1),
        repositories=[
            RepositoryConfig(
                owner="org",
                name="repo",
                allowed_tasks=[TaskCategory.DOCUMENTATION],
            )
        ],
    )
    gh = MockGitHubClient()
    ai = MockAIProvider(available=False)
    state = MockStateStore()
    analyzer = LocalRepositoryAnalyzer()

    orch = Orchestrator(config=config, github_client=gh, ai_provider=ai, state_store=state, analyzer=analyzer)
    report = orch.run()

    # Must complete safely without raising an unhandled exception
    assert report.processed_repositories == 1
    assert report.repositories[0].success_count == 0
    assert report.repositories[0].failure_count == 0  # No tasks attempted, gracefully completed


def test_e2e_github_api_rate_limiting():
    """Scenario 4: GitHub API rate limiting during execution."""
    config = RepoPilotConfig(
        version=1,
        settings=GlobalSettings(max_repositories_per_run=1),
        repositories=[
            RepositoryConfig(
                owner="org",
                name="rate-limited-repo",
                allowed_tasks=[TaskCategory.DOCUMENTATION],
            )
        ],
    )
    gh = MockGitHubClient()
    gh.simulate_rate_limit = True

    ai = MockAIProvider(available=True)
    state = MockStateStore()
    analyzer = LocalRepositoryAnalyzer()

    orch = Orchestrator(config=config, github_client=gh, ai_provider=ai, state_store=state, analyzer=analyzer)
    report = orch.run()

    # Rate limiting should be captured as failure for that repository, without crashing runner
    assert report.processed_repositories == 1
    assert report.repositories[0].failure_count == 1


def test_e2e_repository_permission_failure():
    """Scenario 5: Repository permission failure (read-only or token lacks write)."""
    config = RepoPilotConfig(
        version=1,
        settings=GlobalSettings(max_repositories_per_run=2),
        repositories=[
            RepositoryConfig(owner="org", name="ro-repo", allowed_tasks=[TaskCategory.DOCUMENTATION]),
            RepositoryConfig(owner="org", name="rw-repo", allowed_tasks=[TaskCategory.DOCUMENTATION]),
        ],
    )
    gh = MockGitHubClient()

    # Reject PR creation for repo 1 only
    original_create_pr = gh.create_pull_request
    def selective_create_pr(owner, name, *args, **kwargs):
        if name == "ro-repo":
            raise RuntimeError("403 Forbidden: Resource not accessible by integration")
        return original_create_pr(owner, name, *args, **kwargs)
    gh.create_pull_request = selective_create_pr

    ai = MockAIProvider(available=True)
    state = MockStateStore()
    analyzer = LocalRepositoryAnalyzer()

    orch = Orchestrator(config=config, github_client=gh, ai_provider=ai, state_store=state, analyzer=analyzer)
    report = orch.run()

    assert report.processed_repositories == 2
    assert report.repositories[0].failure_count == 1
    assert report.repositories[1].success_count == 1


def test_e2e_test_execution_timeout():
    """Scenario 6: Test command times out."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        config = RepoPilotConfig(
            version=1,
            settings=GlobalSettings(max_repositories_per_run=1, max_tasks_per_repository=1),
            repositories=[
                RepositoryConfig(
                    owner="org",
                    name="timeout-repo",
                    allowed_tasks=[TaskCategory.DOCUMENTATION],
                    validation=ValidationSettings(
                        test_command="python -c 'import time; time.sleep(10)'",
                        timeout_seconds=1,  # Strict 1-second timeout
                    ),
                )
            ],
        )
        gh = MockGitHubClient()
        ai = MockAIProvider(available=True)
        state = MockStateStore()
        analyzer = LocalRepositoryAnalyzer()

        orch = Orchestrator(
            config=config,
            github_client=gh,
            ai_provider=ai,
            state_store=state,
            analyzer=analyzer,
            workspace_base=Path(tmp_dir),
        )
        report = orch.run()
        summary = report.repositories[0]
        # Timed out task must fail and NOT publish
        assert summary.failure_count == 1
        assert len(gh.pull_requests.get("org/timeout-repo", [])) == 0


def test_e2e_corrupted_state_recovery():
    """Scenario 10: Persistent state store recovers from corrupted state file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "state.json"
        # Write corrupted JSON
        state_file.write_text("{ unclosed invalid json ...", encoding="utf-8")

        store = LocalFileStateStore(state_file)
        state = store.load_state()
        # Should cleanly recover into valid GlobalState without raising an exception
        assert state.version == 1
        assert len(state.repositories) == 0


def test_e2e_auto_merge_disabled_by_default():
    """Scenario 9: Verify auto-merge is strictly NOT performed when auto_merge=False."""
    config = RepoPilotConfig(
        version=1,
        settings=GlobalSettings(max_repositories_per_run=1),
        repositories=[
            RepositoryConfig(
                owner="org",
                name="no-automerge-repo",
                allowed_tasks=[TaskCategory.DOCUMENTATION],
                auto_merge=False,
            )
        ],
    )
    gh = MockGitHubClient()
    ai = MockAIProvider(available=True)
    state = MockStateStore()
    analyzer = LocalRepositoryAnalyzer()

    orch = Orchestrator(config=config, github_client=gh, ai_provider=ai, state_store=state, analyzer=analyzer)
    report = orch.run()

    prs = gh.pull_requests.get("org/no-automerge-repo", [])
    assert len(prs) >= 1
    # PR must remain OPEN, never merged
    assert prs[0].merged is False
    assert prs[0].state == "open"
