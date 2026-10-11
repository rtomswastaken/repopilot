"""Unit tests for persistent state storage, atomic writes, recovery, checkpoints, and deduplication."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import time
import pytest

from repopilot.core.exceptions import StatePersistenceError
from repopilot.core.models import Task, TaskCategory, TaskStatus
from repopilot.github.models import GitHubPullRequest
from repopilot.state.dedup import (
    check_is_duplicate_task,
    generate_task_fingerprint,
    should_reconsider_task,
)
from repopilot.state.models import (
    CheckpointStage,
    FailureCategory,
    GlobalState,
    RepositoryState,
    TaskCheckpoint,
    TaskRecord,
)
from repopilot.state.recovery import find_incomplete_tasks, recover_interrupted_task
from repopilot.state.store import (
    GitBranchStateStore,
    LocalFileStateStore,
    MockStateStore,
    migrate_state,
)


def test_state_creation_and_retrieval():
    """Requirement 6: State creation and retrieval when no state file previously existed."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "state.json"
        store = LocalFileStateStore(state_file)

        # Initial load from non-existent file
        state = store.load_state()
        assert isinstance(state, GlobalState)
        assert state.version == 1
        assert len(state.repositories) == 0

        # Save and retrieve a new repository state
        repo_state = state.get_or_create_repo_state("acme/project")
        repo_state.total_tasks_completed = 5
        store.save_state(state)

        # Reload in a clean store instance
        store2 = LocalFileStateStore(state_file)
        reloaded = store2.load_state()
        assert "acme/project" in reloaded.repositories
        assert reloaded.repositories["acme/project"].total_tasks_completed == 5


def test_state_updates():
    """Requirement 7: State updates accurately track task lifecycle transitions."""
    store = MockStateStore()
    repo = "octocat/hello"

    task = Task(
        id="t-update-1",
        repo_owner="octocat",
        repo_name="hello",
        category=TaskCategory.BUG_FIX,
        title="Fix null pointer exception",
        description="Fix bug",
        status=TaskStatus.IN_PROGRESS,
        related_files=["src/main.py"],
    )

    # 1. Record start
    store.record_task_start(repo, task, starting_revision="rev-123")
    state = store.load_state()
    rec = state.repositories[repo].task_history["t-update-1"]
    assert rec.status == "in_progress"
    assert rec.starting_revision == "rev-123"
    assert rec.retry_count == 0

    # 2. Record validation checkpoint
    store.record_checkpoint(repo, task.id, CheckpointStage.VALIDATION_COMPLETED, {"passed": "True"})
    state = store.load_state()
    rec = state.repositories[repo].task_history["t-update-1"]
    assert rec.validation_passed is True

    # 3. Record outcome
    task.status = TaskStatus.PUBLISHED
    task.pr_number = 42
    task.pr_url = "https://github.com/octocat/hello/pull/42"
    store.record_task_outcome(repo, task)

    state = store.load_state()
    rec = state.repositories[repo].task_history["t-update-1"]
    assert rec.status == "published"
    assert rec.published is True
    assert rec.pr_number == 42
    assert rec.completed_at is not None
    assert state.repositories[repo].total_tasks_completed == 1


def test_atomic_writes_where_applicable():
    """Requirement 8: Atomic writes ensure the state file is never corrupt or half-written."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "atomic_state.json"
        store = LocalFileStateStore(state_file)

        state = store.load_state()
        repo_state = state.get_or_create_repo_state("org/repo")
        repo_state.total_tasks_completed = 10
        store.save_state(state)

        assert state_file.exists()
        # Verify valid JSON
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["repositories"]["org/repo"]["total_tasks_completed"] == 10
        # Verify no orphaned temp files
        tmp_files = list(Path(tmp_dir).glob("*.tmp"))
        assert len(tmp_files) == 0


def test_concurrent_update_conflicts():
    """Requirement 9: Detect concurrent state updates and prevent silent data loss."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "state.json"
        store1 = LocalFileStateStore(state_file, track_conflicts=True)
        store2 = LocalFileStateStore(state_file, track_conflicts=True)

        # Store 1 saves initial state
        state1 = store1.load_state()
        state1.get_or_create_repo_state("org/repo").total_tasks_completed = 1
        store1.save_state(state1)

        # Store 2 loads valid state
        state2 = store2.load_state()

        # Simulate store 1 modifying disk state concurrently
        time.sleep(0.05)
        state1.get_or_create_repo_state("org/repo").total_tasks_completed = 2
        store1.save_state(state1)

        # Store 2 now attempts to save stale state -> should raise conflict error
        time.sleep(0.05)
        state2.get_or_create_repo_state("org/repo").total_tasks_completed = 3
        with pytest.raises(StatePersistenceError) as exc_info:
            store2.save_state(state2)
        assert "Concurrent state update conflict detected" in str(exc_info.value)


def test_corrupted_state_recovery():
    """Requirement 10: Automatic recovery from unreadable or corrupted state files."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "corrupted_state.json"
        backup_file = state_file.with_suffix(".bak")
        store = LocalFileStateStore(state_file)

        # Save initial valid state to create backup
        valid_state = GlobalState()
        valid_state.get_or_create_repo_state("org/backup-repo").total_tasks_completed = 7
        store.save_state(valid_state)

        # Corrupt the primary state file with garbage
        state_file.write_text("{broken json syntax: unexpected token", encoding="utf-8")

        # Load should recover from .bak backup file
        recovered = store.load_state()
        assert "org/backup-repo" in recovered.repositories
        assert recovered.repositories["org/backup-repo"].total_tasks_completed == 7

        # Now test case where backup is ALSO corrupted or missing
        state_file.write_text("<<<fatal syntax>>>", encoding="utf-8")
        backup_file.write_text("<<<also corrupted>>>", encoding="utf-8")

        fresh = store.load_state()
        assert isinstance(fresh, GlobalState)
        assert len(fresh.repositories) == 0

        # Verify corrupted file was quarantined
        quarantined = list(Path(tmp_dir).glob("*.corrupted.*"))
        assert len(quarantined) >= 1


def test_task_deduplication():
    """Requirement 11: Task deduplication detects identical IDs, titles, and overlapping files."""
    store = MockStateStore()
    repo = "owner/project"

    task = Task(
        id="task-100",
        repo_owner="owner",
        repo_name="project",
        category=TaskCategory.DOCUMENTATION,
        title="Update API documentation",
        description="Docs update",
        status=TaskStatus.PUBLISHED,
        related_files=["docs/api.md"],
    )
    store.record_task_start(repo, task)
    task.status = TaskStatus.PUBLISHED
    task.pr_number = 1
    store.record_task_outcome(repo, task)

    # 1. Exact task ID duplicate
    assert store.is_duplicate_task(repo, "Random Title", ["different.md"], task_id="task-100") is True

    # 2. Normalized title duplicate
    assert store.is_duplicate_task(repo, "update api documentation", ["other.md"]) is True

    # 3. File overlap duplicate for completed tasks
    assert store.is_duplicate_task(repo, "Completely New Title", ["docs/api.md"]) is True

    # 4. Unrelated new task is NOT duplicate
    assert store.is_duplicate_task(repo, "Implement Auth Feature", ["src/auth.py"]) is False


def test_reconsidering_old_failed_tasks():
    """Requirement 12: Rules allow reconsidering old failed tasks when revision changes or cooldown expires."""
    repo_state = RepositoryState(repository="owner/project")

    # Failed task at old revision rev-1
    record = TaskRecord(
        task_id="failed-1",
        category="bug_fix",
        title="Fix flaky test",
        status="failed",
        starting_revision="rev-1",
        is_recoverable=True,
        retry_count=1,
        max_retries=2,
        last_attempted_at=datetime.utcnow() - timedelta(hours=48),
    )
    repo_state.task_history["failed-1"] = record

    # At same revision and within limit, if cooldown elapsed -> eligible for reconsideration
    assert should_reconsider_task(record, current_revision="rev-1", cooldown_hours=24) is True

    # When repository revision changed (e.g. rev-2) -> eligible for reconsideration!
    assert should_reconsider_task(record, current_revision="rev-2") is True

    # Check deduplication helper respects reconsideration
    is_dup = check_is_duplicate_task(
        repo_state=repo_state,
        title="Fix flaky test",
        related_files=[],
        task_id="failed-1",
        current_revision="rev-2",
    )
    assert is_dup is False  # Not considered duplicate because it is eligible to be reconsidered!

    # Completed tasks should NEVER be reconsidered even if revision changed
    record.status = "published"
    record.published = True
    assert should_reconsider_task(record, current_revision="rev-3") is False


def test_checkpoint_creation():
    """Requirement 13: Checkpoint transitions are recorded with sanitized metadata."""
    store = MockStateStore()
    repo = "owner/repo"

    task = Task(
        id="t-cp-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.CODE_QUALITY,
        title="Refactor auth helpers",
        description="Refactor",
        status=TaskStatus.IN_PROGRESS,
    )
    store.record_task_start(repo, task)

    # Record milestones
    store.record_checkpoint(repo, task.id, CheckpointStage.PLAN_CREATED, {"files_count": 2})
    store.record_checkpoint(repo, task.id, CheckpointStage.VALIDATION_COMPLETED, {"passed": "True"})
    store.record_checkpoint(
        repo,
        task.id,
        CheckpointStage.PULL_REQUEST_CREATED,
        {"pr_number": "12", "pr_url": "https://github.com/owner/repo/pull/12", "token": "ghp_secretTokenToScrub123456789"},
    )

    state = store.load_state()
    rec = state.repositories[repo].task_history["t-cp-1"]
    assert len(rec.checkpoints) >= 3
    assert rec.last_checkpoint_stage == CheckpointStage.PULL_REQUEST_CREATED
    assert rec.pr_number == 12
    # Verify secrets were scrubbed from checkpoint data
    for cp in rec.checkpoints:
        for k, v in cp.data.items():
            assert "ghp_" not in str(v)


def test_recovery_after_interruption():
    """Requirement 14: Recover interrupted tasks and prevent duplicate PR creation."""
    store = MockStateStore()
    repo = "owner/repo"

    task = Task(
        id="interrupted-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.BUG_FIX,
        title="Fix memory leak",
        description="Fix leak",
        status=TaskStatus.IN_PROGRESS,
    )
    store.record_task_start(repo, task)

    # Simulate crash right after PR was created
    store.record_checkpoint(
        repo,
        task.id,
        CheckpointStage.PULL_REQUEST_CREATED,
        {"pr_number": "99", "pr_url": "https://github.com/owner/repo/pull/99"},
    )

    # Incomplete tasks detected
    incomplete = store.get_incomplete_tasks(repo)
    assert len(incomplete) == 1
    assert incomplete[0][1].task_id == "interrupted-1"

    # Recover without duplicating PR
    outcomes = store.recover_incomplete_tasks()
    assert len(outcomes) == 1
    assert outcomes[0].recovered is True
    assert outcomes[0].new_status == "published"

    state = store.load_state()
    rec = state.repositories[repo].task_history["interrupted-1"]
    assert rec.status == "published"
    assert rec.published is True
    assert rec.pr_number == 99


def test_retry_exhaustion():
    """Requirement 15: Tasks exceeding max_retries are marked permanently failed."""
    repo_state = RepositoryState(repository="owner/repo")
    record = TaskRecord(
        task_id="exhausted-task",
        category="bug_fix",
        title="Flaky task",
        status="in_progress",
        retry_count=2,
        max_retries=2,
        checkpoints=[
            TaskCheckpoint(task_id="exhausted-task", stage=CheckpointStage.EXECUTION_STARTED),
        ],
    )
    repo_state.task_history["exhausted-task"] = record

    outcome = recover_interrupted_task(repo_state, "exhausted-task")
    assert outcome.recovered is True
    assert outcome.new_status == "failed"
    assert "exhausted" in record.error_message.lower()


def test_failure_classification():
    """Requirement 16: Failure records are classified and diagnostics sanitized."""
    store = MockStateStore()
    repo = "owner/repo"

    task = Task(
        id="fail-task-1",
        repo_owner="owner",
        repo_name="repo",
        category=TaskCategory.TESTS,
        title="Add tests",
        description="Add tests",
        status=TaskStatus.IN_PROGRESS,
    )
    store.record_task_start(repo, task)

    # Record specific failures
    store.record_task_failure(
        repo_fullname=repo,
        task=task,
        category=FailureCategory.AI_PROVIDER_UNAVAILABLE,
        stage="planning",
        diagnostic="HTTP 429 quota exhausted with token ghp_secret12345678901234567890",
        is_recoverable=True,
    )

    state = store.load_state()
    rec = state.repositories[repo].task_history["fail-task-1"]
    assert len(rec.failure_history) == 1
    fail_rec = rec.failure_history[0]
    assert fail_rec.category == FailureCategory.AI_PROVIDER_UNAVAILABLE
    assert fail_rec.stage == "planning"
    assert fail_rec.is_recoverable is True
    assert "ghp_" not in fail_rec.diagnostic_info
    assert "[REDACTED_TOKEN]" in fail_rec.diagnostic_info


def test_persistent_state_unavailable():
    """Requirement 21: Explicit failure when persistent state store is unavailable."""
    # Invalid directory path that cannot be created
    invalid_path = Path("/dev/null/forbidden_sub_dir/state.json")
    store = LocalFileStateStore(invalid_path)
    state = GlobalState()

    with pytest.raises(StatePersistenceError) as exc_info:
        store.save_state(state)
    assert "directory unavailable" in str(exc_info.value) or "Failed to persist state" in str(exc_info.value)


def test_state_schema_migration():
    """Requirement 22: Safe migration of older or unversioned state schemas."""
    legacy_data = {
        "version": 0,
        "last_run_id": "run-legacy-1",
        "repositories": {
            "alice/project": {
                "total_tasks_completed": 4,
                "total_tasks_failed": 1,
                "task_history": {
                    "t-legacy": {
                        "task_id": "t-legacy",
                        "category": "bug_fix",
                        "title": "Legacy fix",
                        "status": "published",
                    }
                },
            }
        },
    }

    migrated = migrate_state(legacy_data)
    assert isinstance(migrated, GlobalState)
    assert migrated.version == 1
    assert "alice/project" in migrated.repositories
    repo = migrated.repositories["alice/project"]
    assert repo.repository == "alice/project"
    assert repo.total_tasks_completed == 4
    assert "t-legacy" in repo.task_history
    assert repo.task_history["t-legacy"].status == "published"


def test_git_branch_state_store_plumbing():
    """Verify GitBranchStateStore commits to orphan state branch using git plumbing."""
    with tempfile.TemporaryDirectory() as git_tmp:
        repo_dir = Path(git_tmp)
        # Initialize a temporary git repository
        subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@repopilot.local"], cwd=str(repo_dir), check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "RepoPilot Test"], cwd=str(repo_dir), check=True, capture_output=True)

        branch_store = GitBranchStateStore(
            branch_name="repopilot-state-test",
            file_rel_path="state.json",
            git_dir=repo_dir,
        )

        # Initial state save
        state = GlobalState()
        repo_st = state.get_or_create_repo_state("git/test-repo")
        repo_st.total_tasks_completed = 3
        branch_store.save_state(state)

        # Verify state branch was created and contains commit
        res = subprocess.run(
            ["git", "show", "repopilot-state-test:state.json"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )
        loaded_json = json.loads(res.stdout)
        assert loaded_json["repositories"]["git/test-repo"]["total_tasks_completed"] == 3

        # Read back via branch store
        reloaded = branch_store.load_state()
        assert reloaded.repositories["git/test-repo"].total_tasks_completed == 3
