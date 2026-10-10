"""Safe patch application with strict security checks against path traversal and prohibited targets."""

import fnmatch
import logging
from pathlib import Path
from typing import List

from repopilot.core.exceptions import SecurityViolationError
from repopilot.core.models import ChangeAction, ChangePlan, FileChange

logger = logging.getLogger(__name__)


class PatchValidator:
    """Enforces strict security checks on proposed file changes."""

    def __init__(self, workspace_root: Path, excluded_patterns: List[str]):
        self.workspace_root = workspace_root.resolve()
        self.excluded_patterns = excluded_patterns

    def is_path_excluded(self, relative_path: str) -> bool:
        """Check if relative_path matches any configured excluded patterns."""
        norm_path = relative_path.replace("\\", "/").lstrip("/")

        for pattern in self.excluded_patterns:
            pattern_norm = pattern.replace("\\", "/").rstrip("/")
            # Directory match
            if pattern.endswith("/") and (norm_path.startswith(pattern_norm + "/") or norm_path == pattern_norm):
                return True
            # Glob / fnmatch match
            if fnmatch.fnmatch(norm_path, pattern) or fnmatch.fnmatch(norm_path, f"*{pattern}*"):
                return True
            # Substring exact check
            if pattern_norm in norm_path:
                return True
        return False

    def validate_file_change(self, change: FileChange) -> Path:
        """Validate a single change for path traversal and security exclusion.

        Returns the resolved safe target Path within the workspace.
        """
        raw_path = change.path.strip()

        # Reject absolute paths or empty paths
        if not raw_path or Path(raw_path).is_absolute():
            raise SecurityViolationError(f"Absolute or empty file paths are prohibited: '{raw_path}'")

        # Resolve path within workspace
        target = (self.workspace_root / raw_path).resolve()

        # Path traversal defense: target must reside strictly inside workspace_root
        try:
            target.relative_to(self.workspace_root)
        except ValueError:
            raise SecurityViolationError(f"Path traversal detected: '{raw_path}' attempts to escape workspace.")

        # Excluded paths check
        rel_to_root = target.relative_to(self.workspace_root).as_posix()
        if self.is_path_excluded(rel_to_root):
            raise SecurityViolationError(
                f"Modifying path '{rel_to_root}' is forbidden. It matches an excluded path rule."
            )

        return target


class PatchApplier:
    """Safely applies changes to an isolated workspace."""

    def __init__(self, workspace_root: Path, excluded_patterns: List[str]):
        self.workspace_root = workspace_root.resolve()
        self.validator = PatchValidator(self.workspace_root, excluded_patterns)

    def apply_plan(self, plan: ChangePlan) -> List[Path]:
        """Validate and apply all file changes in the ChangePlan.

        Returns list of modified Paths.
        """
        # Phase 1: Pre-validation of all files before making any modifications
        targets: List[Path] = []
        for change in plan.files:
            target_path = self.validator.validate_file_change(change)
            targets.append(target_path)

        # Phase 2: Execute verified modifications
        modified_paths: List[Path] = []
        for change, target_path in zip(plan.files, targets):
            if change.action in (ChangeAction.CREATE, ChangeAction.MODIFY):
                target_path.parent.mkdir(parents=True, exist_ok=True)
                content = change.content or ""
                target_path.write_text(content, encoding="utf-8")
                modified_paths.append(target_path)
                logger.info(f"Applied change ({change.action.value}) to: {target_path.relative_to(self.workspace_root)}")

            elif change.action == ChangeAction.DELETE:
                if target_path.exists() and target_path.is_file():
                    target_path.unlink()
                    modified_paths.append(target_path)
                    logger.info(f"Deleted file: {target_path.relative_to(self.workspace_root)}")

        return modified_paths
