"""Unit tests for security boundaries, path traversal defense, and isolated environments."""

import os
from pathlib import Path
import tempfile
import pytest

from repopilot.config.models import DEFAULT_MANDATORY_EXCLUDED_PATHS, ValidationSettings
from repopilot.core.exceptions import SecurityViolationError
from repopilot.core.models import ChangeAction, ChangePlan, FileChange
from repopilot.execution.patch import PatchApplier, PatchValidator
from repopilot.execution.workspace import IsolatedWorkspace
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
            "secrets/api_keys.json",
            "production_credentials/db.pem",
            ".env",
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

        sanitized = runner._get_sanitized_env()
        assert "GITHUB_TOKEN" not in sanitized
        assert "GEMINI_API_KEY" not in sanitized
