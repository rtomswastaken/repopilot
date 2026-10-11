"""Safe patch application with strict security checks against path traversal, symlink attacks, and prohibited targets."""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from repopilot.core.exceptions import (
    ConflictingPatchError,
    PatchApplicationError,
    ResourceLimitExceededError,
    SecurityViolationError,
)
from repopilot.core.models import ChangeAction, ChangePlan, FileChange
from repopilot.execution.limits import ResourceLimits
from repopilot.execution.security import PathSecurityChecker

logger = logging.getLogger(__name__)


class PatchValidator:
    """Enforces strict structural, resource, and security checks on proposed file changes."""

    def __init__(
        self,
        workspace_root: Path,
        excluded_patterns: Optional[List[str]] = None,
        limits: Optional[ResourceLimits] = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.security_checker = PathSecurityChecker(self.workspace_root, excluded_patterns)
        self.limits = limits or ResourceLimits()

    def is_path_excluded(self, relative_path: str) -> bool:
        """Check if relative_path matches any configured excluded patterns."""
        return self.security_checker.is_path_excluded(relative_path)

    def validate_file_change(self, change: FileChange) -> Path:
        """Validate a single change for path traversal, symlink safety, and exclusions.

        Returns the resolved safe target Path within the workspace.
        """
        # Validate structure
        if not change.path or not isinstance(change.path, str):
            raise SecurityViolationError("File change path must be a non-empty string.")

        # Check content limits
        if change.content is not None:
            content_bytes = len(change.content.encode("utf-8", errors="replace"))
            if content_bytes > self.limits.max_file_size_bytes:
                raise ResourceLimitExceededError(
                    f"File '{change.path}' exceeds maximum size limit ({content_bytes} > {self.limits.max_file_size_bytes} bytes)."
                )

        # Security containment check
        return self.security_checker.resolve_and_verify(change.path)

    def validate_plan(self, plan: ChangePlan, current_revision: Optional[str] = None) -> List[Path]:
        """Validate an entire ChangePlan against structural, conflict, and resource limits.
        
        Returns the resolved list of target Paths.
        """
        if not plan.files:
            return []

        # Check maximum file count limit
        if len(plan.files) > self.limits.max_changed_files:
            raise ResourceLimitExceededError(
                f"Patch contains {len(plan.files)} files, exceeding limit of {self.limits.max_changed_files}."
            )

        # Check total patch size
        total_size = sum(
            len((f.content or "").encode("utf-8", errors="replace"))
            for f in plan.files
            if f.content
        )
        if total_size > self.limits.max_patch_size_bytes:
            raise ResourceLimitExceededError(
                f"Total patch size of {total_size} bytes exceeds limit of {self.limits.max_patch_size_bytes} bytes."
            )

        # Detect conflicting / duplicate paths in plan
        seen_paths: Dict[str, FileChange] = {}
        targets: List[Path] = []

        for change in plan.files:
            norm_key = change.path.strip().replace("\\", "/").lstrip("/")
            if norm_key in seen_paths:
                prev = seen_paths[norm_key]
                if prev.action != change.action or prev.content != change.content:
                    raise ConflictingPatchError(
                        f"Conflicting modifications detected for '{norm_key}' in the same change plan."
                    )
            seen_paths[norm_key] = change

            target_path = self.validate_file_change(change)
            targets.append(target_path)

        return targets


class PatchApplier:
    """Safely applies changes to an isolated workspace with transactional rollback on failure."""

    def __init__(
        self,
        workspace_root: Path,
        excluded_patterns: Optional[List[str]] = None,
        limits: Optional[ResourceLimits] = None,
    ):
        self.workspace_root = workspace_root.resolve()
        self.limits = limits or ResourceLimits()
        self.validator = PatchValidator(self.workspace_root, excluded_patterns, self.limits)

    def apply_plan(
        self,
        plan: ChangePlan,
        target_revision: Optional[str] = None,
        current_revision: Optional[str] = None,
    ) -> List[Path]:
        """Validate and transactionally apply all file changes in the ChangePlan.

        If target_revision is specified and does not match current_revision, application is rejected.
        If any modification fails, original workspace state for all target files is restored.
        Returns list of modified Paths.
        """
        # Phase 1: Revision verification
        if target_revision and current_revision and target_revision != current_revision:
            raise PatchApplicationError(
                f"Target revision mismatch: expected '{target_revision}', but workspace is at '{current_revision}'."
            )

        # Phase 2: Pre-validation of all files before touching anything
        targets = self.validator.validate_plan(plan, current_revision=current_revision)

        # Phase 3: Transactional snapshot of affected files
        backups: Dict[Path, Optional[str]] = {}
        for target_path in targets:
            if target_path.exists():
                try:
                    backups[target_path] = target_path.read_text(encoding="utf-8")
                except Exception:
                    # If binary or unreadable as text
                    backups[target_path] = None
            else:
                backups[target_path] = None  # Did not exist before

        # Phase 4: Execute modifications with rollback on any failure
        modified_paths: List[Path] = []
        created_paths: List[Path] = []

        try:
            for change, target_path in zip(plan.files, targets):
                if change.action in (ChangeAction.CREATE, ChangeAction.MODIFY):
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    content = change.content or ""
                    did_exist = backups[target_path] is not None
                    target_path.write_text(content, encoding="utf-8")
                    modified_paths.append(target_path)
                    if not did_exist:
                        created_paths.append(target_path)
                    logger.info(
                        f"Applied change ({change.action.value}) to: {target_path.relative_to(self.workspace_root)}"
                    )

                elif change.action == ChangeAction.DELETE:
                    if target_path.exists() and target_path.is_file():
                        target_path.unlink()
                        modified_paths.append(target_path)
                        logger.info(f"Deleted file: {target_path.relative_to(self.workspace_root)}")

            return modified_paths

        except Exception as e:
            # Transactional Rollback
            logger.error(f"Error applying patch, rolling back all modifications: {e}")
            for target_path, orig_content in backups.items():
                try:
                    if orig_content is not None:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        target_path.write_text(orig_content, encoding="utf-8")
                    elif target_path in created_paths and target_path.exists():
                        target_path.unlink()
                except Exception as rollback_err:
                    logger.error(f"Rollback failed for {target_path}: {rollback_err}")

            if isinstance(e, (SecurityViolationError, PatchApplicationError, ResourceLimitExceededError)):
                raise
            raise PatchApplicationError(f"Failed to apply patch safely: {e}") from e
