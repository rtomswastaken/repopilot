"""Unit tests for security boundaries, path traversal defense, and isolated environments."""

import os
from pathlib import Path
import tempfile
import pytest

from repopilot.config.models import DEFAULT_MANDATORY_EXCLUDED_PATHS, ValidationSettings
from repopilot.core.exceptions import (
    ConflictingPatchError,
    PathTraversalError,
    ProhibitedPathError,
    ResourceLimitExceededError,
    SecurityViolationError,
    SymlinkEscapeError,
)
from repopilot.core.models import ChangeAction, ChangePlan, FileChange
from repopilot.execution.limits import ResourceLimits
from repopilot.execution.patch import PatchApplier, PatchValidator
from repopilot.execution.security import SecretSanitizer
from repopilot.execution.workspace import IsolatedWorkspace, WorkspaceManager
from repopilot.validation.runner import ValidationRunner


def test_path_traversal_prevention():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        validator = PatchValidator(root, DEFAULT_MANDATORY_EXCLUDED_PATHS)

        # Attempt to escape via relative parent directory
        change_traversal = FileChange(
            path="../outside_file.py",
            action=ChangeAction.CREATE,
            content="malicious",
        )
        with pytest.raises(SecurityViolationError, match="Path traversal detected"):
            validator.validate_file_change(change_traversal)

        # Attempt complex relative traversal
        change_complex = FileChange(
            path="sub/../../escape.py",
            action=ChangeAction.CREATE,
            content="malicious",
        )
        with pytest.raises(SecurityViolationError, match="Path traversal detected"):
            validator.validate_file_change(change_complex)

        # Attempt absolute path
        change_abs = FileChange(
            path="/etc/passwd",
            action=ChangeAction.MODIFY,
            content="malicious",
        )
        with pytest.raises(SecurityViolationError, match="Absolute or empty"):
            validator.validate_file_change(change_abs)


def test_forbidden_excluded_paths_rejected():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        validator = PatchValidator(root, DEFAULT_MANDATORY_EXCLUDED_PATHS)

        forbidden_targets = [
            ".github/workflows/deploy.yml",
            ".github/workflows/ci.yaml",
            "secrets/api_keys.json",
            "production_credentials/db.pem",
            ".env",
            ".git/config",
            ".ssh/id_rsa",
            ".aws/credentials",
            ".bashrc",
        ]

        for target in forbidden_targets:
            change = FileChange(
                path=target,
                action=ChangeAction.MODIFY,
                content="untrusted modification",
            )
            with pytest.raises(SecurityViolationError, match="forbidden"):
                validator.validate_file_change(change)


def test_safe_patch_application():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        applier = PatchApplier(root, DEFAULT_MANDATORY_EXCLUDED_PATHS)

        plan = ChangePlan(
            task_id="t-safe",
            title="Safe change",
            description="Add safe helper",
            files=[
                FileChange(
                    path="src/helpers/math.py",
                    action=ChangeAction.CREATE,
                    content="def add(a, b): return a + b\n",
                )
            ],
        )

        modified = applier.apply_plan(plan)
        assert len(modified) == 1
        created_file = root / "src/helpers/math.py"
        assert created_file.exists()
        assert "def add" in created_file.read_text()


def test_workspace_automatic_cleanup():
    ws_path = None
    with IsolatedWorkspace() as ws:
        ws_path = ws.path
        assert ws_path is not None
        assert ws_path.exists()
        (ws_path / "scratch.txt").write_text("temporary data")

    # After exit, workspace should be deleted
    assert not ws_path.exists()


def test_validation_runner_strips_privileged_env():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        runner = ValidationRunner(root, ValidationSettings(timeout_seconds=30))

        # Inject fake sensitive tokens into process env
        os.environ["GITHUB_TOKEN"] = "sensitive_gh_token_12345"
        os.environ["GEMINI_API_KEY"] = "sensitive_ai_key_67890"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "secret_aws_123"
        os.environ["CUSTOM_API_KEY"] = "custom_secret_key"

        sanitized = runner._get_sanitized_env()
        assert "GITHUB_TOKEN" not in sanitized
        assert "GEMINI_API_KEY" not in sanitized
        assert "AWS_SECRET_ACCESS_KEY" not in sanitized
        assert "CUSTOM_API_KEY" not in sanitized


def test_symlink_escape_attempts():
    """Verify that symlinks attempting to escape workspace are detected and rejected."""
    with tempfile.TemporaryDirectory() as tmp_outside:
        outside_path = Path(tmp_outside) / "sensitive_outside.txt"
        outside_path.write_text("classified outside data")

        with tempfile.TemporaryDirectory() as tmp_ws:
            ws_root = Path(tmp_ws)
            # Create a symlink pointing to an outside directory
            symlink_in_ws = ws_root / "evil_link"
            symlink_in_ws.symlink_to(Path(tmp_outside))

            validator = PatchValidator(ws_root, DEFAULT_MANDATORY_EXCLUDED_PATHS)
            change = FileChange(
                path="evil_link/sensitive_outside.txt",
                action=ChangeAction.MODIFY,
                content="malicious overwrite",
            )
            with pytest.raises(SecurityViolationError, match="Symlink escape detected"):
                validator.validate_file_change(change)


def test_invalid_patch_rejection():
    """Verify that malformed patches with null bytes, empty paths, or conflicting targets are rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        validator = PatchValidator(root, DEFAULT_MANDATORY_EXCLUDED_PATHS)

        # Null byte in path
        with pytest.raises(SecurityViolationError, match="Null byte"):
            validator.validate_file_change(FileChange(path="src/file\0.py", content=""))

        # Empty path
        with pytest.raises(SecurityViolationError, match="(?i)empty"):
            validator.validate_file_change(FileChange(path="   ", content=""))


        # Conflicting changes in single plan
        plan = ChangePlan(
            task_id="t-conflict",
            title="Conflict plan",
            description="Two conflicting changes for same file",
            files=[
                FileChange(path="src/app.py", action=ChangeAction.CREATE, content="version A"),
                FileChange(path="src/app.py", action=ChangeAction.CREATE, content="version B"),
            ],
        )
        with pytest.raises(ConflictingPatchError, match="Conflicting modifications"):
            validator.validate_plan(plan)


def test_excessive_patch_size():
    """Verify that resource limits on file count and patch size are enforced."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        limits = ResourceLimits(
            max_changed_files=2,
            max_file_size_bytes=100,
            max_patch_size_bytes=150,
        )
        validator = PatchValidator(root, DEFAULT_MANDATORY_EXCLUDED_PATHS, limits=limits)

        # Too many files
        plan_too_many = ChangePlan(
            task_id="t-count",
            title="Too many files",
            description="Exceeds 2 files",
            files=[
                FileChange(path="f1.py", content="a"),
                FileChange(path="f2.py", content="b"),
                FileChange(path="f3.py", content="c"),
            ],
        )
        with pytest.raises(ResourceLimitExceededError, match="exceeding limit of 2"):
            validator.validate_plan(plan_too_many)

        # File size exceeds limit
        plan_large_file = ChangePlan(
            task_id="t-large-file",
            title="Large file",
            description="Exceeds 100 bytes",
            files=[
                FileChange(path="f1.py", content="X" * 150),
            ],
        )
        with pytest.raises(ResourceLimitExceededError, match="exceeds maximum size limit"):
            validator.validate_plan(plan_large_file)


def test_patch_application_transactional_rollback():
    """Verify that if a patch application fails mid-way, original workspace state is restored."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        orig_file = root / "src" / "keep.py"
        orig_file.parent.mkdir(parents=True)
        orig_file.write_text("original content")

        applier = PatchApplier(root, DEFAULT_MANDATORY_EXCLUDED_PATHS)

        # Plan modifies keep.py, but then has a forbidden path
        plan = ChangePlan(
            task_id="t-fail",
            title="Partial failure",
            description="First succeeds, second forbidden",
            files=[
                FileChange(path="src/keep.py", action=ChangeAction.MODIFY, content="corrupted content"),
                FileChange(path=".github/workflows/ci.yml", action=ChangeAction.CREATE, content="bad"),
            ],
        )

        with pytest.raises(SecurityViolationError):
            applier.apply_plan(plan)

        # Original content must be strictly preserved
        assert orig_file.read_text() == "original content"


def test_starting_revision_and_modified_file_tracking():
    """Verify tracking of starting revision and modified files in workspace."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "initial.py").write_text("print('hello')")

        with IsolatedWorkspace() as ws:
            ws.populate_from(root)
            rev1 = ws.get_starting_revision()
            assert rev1 is not None

            # Track modifications
            ws.track_modified_file("src/helper.py")
            ws.track_modified_file("initial.py")
            modified = ws.get_modified_files()
            assert "initial.py" in modified
            assert "src/helper.py" in modified


def test_multiple_isolated_tasks():
    """Verify that multiple task workspaces operate independently without shared state."""
    manager = WorkspaceManager()
    ws1 = manager.create_workspace("task-1")
    ws2 = manager.create_workspace("task-2")

    assert ws1.path != ws2.path
    assert ws1.path.exists() and ws2.path.exists()

    p1 = ws1.path
    p2 = ws2.path

    (ws1.path / "file1.txt").write_text("data 1")
    assert not (ws2.path / "file1.txt").exists()

    manager.cleanup_all()
    assert not p1.exists()
    assert not p2.exists()



def test_cleanup_after_exceptions():
    """Verify workspace cleanup occurs reliably even when unhandled exceptions occur."""
    ws_path = None
    try:
        with IsolatedWorkspace() as ws:
            ws_path = ws.path
            (ws_path / "data.txt").write_text("data")
            raise RuntimeError("Simulated crash inside workspace")
    except RuntimeError:
        pass

    assert ws_path is not None
    assert not ws_path.exists()


def test_interrupted_execution_cleanup():
    """Verify workspace handles KeyboardInterrupt cleanly."""
    ws_path = None
    try:
        with IsolatedWorkspace() as ws:
            ws_path = ws.path
            (ws_path / "temp.txt").write_text("interrupt me")
            raise KeyboardInterrupt()
    except KeyboardInterrupt:
        pass

    assert ws_path is not None
    assert not ws_path.exists()


def test_disk_limit_exceeded():
    """Verify workspace disk quota enforcement."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        # Create a 200-byte file
        (root / "data.bin").write_bytes(b"A" * 200)

        with IsolatedWorkspace(max_disk_bytes=100) as ws:
            with pytest.raises(ResourceLimitExceededError, match="disk usage exceeded"):
                ws.populate_from(root)
