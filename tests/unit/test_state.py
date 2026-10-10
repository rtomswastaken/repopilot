"""Unit tests for persistent state storage, atomic writes, and duplicate detection."""

import tempfile
from pathlib import Path
from repopilot.core.models import Task, TaskCategory, TaskStatus
from repopilot.state.models import GlobalState
from repopilot.state.store import LocalFileStateStore, MockStateStore


def test_mock_state_store_duplicate_detection():
    store = MockStateStore()
    repo = "owner/repo"

    task = Task(
        id="t-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.DOCUMENTATION,
        title="Add documentation for API",
        description="Docs update",
        status=TaskStatus.DISCOVERED,
        related_files=["docs/api.md"],
    )

    # Initially not duplicate
    assert store.is_duplicate_task(repo, task.title, task.related_files) is False

    # Start and complete task
    store.record_task_start(repo, task)
    task.status = TaskStatus.PUBLISHED
    task.pr_url = "https://github.com/owner/repo/pull/42"
    store.record_task_outcome(repo, task)

    # Now should be detected as duplicate by title
    assert store.is_duplicate_task(repo, "Add documentation for API", []) is True
    # And by matching related files
    assert store.is_duplicate_task(repo, "Different Title", ["docs/api.md"]) is True


def test_local_file_state_store_atomic_write():
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "sub" / "state.json"
        store = LocalFileStateStore(state_file)

        # Initial state should be empty
        state = store.load_state()
        assert isinstance(state, GlobalState)
        assert len(state.repositories) == 0

        # Save updated state
        repo_state = state.get_or_create_repo_state("alice/project")
        repo_state.total_tasks_completed = 3
        store.save_state(state)

        assert state_file.exists()

        # Reload from clean store instance
        store2 = LocalFileStateStore(state_file)
        reloaded = store2.load_state()
        assert "alice/project" in reloaded.repositories
        assert reloaded.repositories["alice/project"].total_tasks_completed == 3
