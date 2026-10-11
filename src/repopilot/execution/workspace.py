"""Isolated workspace management for safe code execution and patch application."""

import hashlib
import logging
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Dict, List, Optional, Set

from repopilot.core.exceptions import (
    ResourceLimitExceededError,
    SecurityViolationError,
    SymlinkEscapeError,
)
from repopilot.execution.limits import check_directory_size

logger = logging.getLogger(__name__)


def _remove_readonly(func, path, excinfo):
    """Clear readonly bit and retry deletion on Windows/POSIX."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        logger.warning(f"Could not force-remove {path}: {e}")


class IsolatedWorkspace:
    """Provides a temporary, strictly isolated workspace directory.

    Guarantees automatic cleanup upon exit, starting revision tracking,
    modified file tracking, and disk limit enforcement.
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        prefix: str = "repopilot_ws_",
        task_id: Optional[str] = None,
        max_disk_bytes: Optional[int] = None,
    ):
        self.base_dir = Path(base_dir).resolve() if base_dir else None
        self.prefix = prefix
        self.task_id = task_id
        self.max_disk_bytes = max_disk_bytes
        self.path: Optional[Path] = None
        self._starting_revision: Optional[str] = None
        self._modified_files: Set[str] = set()
        self._snapshots: Dict[str, Dict[str, str]] = {}

    def __enter__(self) -> "IsolatedWorkspace":
        if self.base_dir:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix=self.prefix, dir=self.base_dir)
        self.path = Path(temp_dir).resolve()
        logger.debug(f"Created isolated workspace at: {self.path} (task: {self.task_id})")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self) -> None:
        """Safely clean up the isolated workspace directory."""
        if self.path and self.path.exists():
            try:
                shutil.rmtree(self.path, onerror=_remove_readonly)
                logger.debug(f"Cleaned up isolated workspace at: {self.path}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary workspace {self.path}: {e}")
            finally:
                self.path = None

    def record_starting_revision(self, revision_or_commit: Optional[str] = None) -> str:
        """Record the repository's starting revision.
        
        If revision_or_commit is not provided, attempts to read git HEAD commit SHA,
        or computes a SHA256 hash manifest of workspace files.
        """
        if not self.path:
            raise RuntimeError("Workspace has not been entered.")

        if revision_or_commit:
            self._starting_revision = revision_or_commit
            return self._starting_revision

        # Check if workspace has a git directory
        git_dir = self.path / ".git"
        if git_dir.exists():
            try:
                proc = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(self.path),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    self._starting_revision = proc.stdout.strip()
                    return self._starting_revision
            except Exception:
                pass

        # Fallback: compute hash of existing files manifest
        hasher = hashlib.sha256()
        for root, dirs, files in os.walk(self.path):
            dirs.sort()
            for f in sorted(files):
                fp = Path(root) / f
                if not fp.is_symlink():
                    try:
                        hasher.update(fp.relative_to(self.path).as_posix().encode())
                        hasher.update(str(fp.stat().st_size).encode())
                    except OSError:
                        pass

        self._starting_revision = hasher.hexdigest()[:16]
        return self._starting_revision

    def get_starting_revision(self) -> Optional[str]:
        """Return the recorded starting revision."""
        return self._starting_revision

    def track_modified_file(self, relative_path: str) -> None:
        """Record that a relative path was modified in this workspace."""
        norm_path = relative_path.replace("\\", "/").strip().lstrip("/")
        self._modified_files.add(norm_path)

    def get_modified_files(self) -> List[str]:
        """Return a sorted list of all tracked modified files."""
        return sorted(list(self._modified_files))

    def check_disk_usage(self) -> int:
        """Verify current workspace disk usage against configured limits."""
        if not self.path or not self.path.exists():
            return 0
        if self.max_disk_bytes is not None:
            return check_directory_size(self.path, self.max_disk_bytes)
        return check_directory_size(self.path, 1024 * 1024 * 1024 * 10)  # Default 10GB sanity limit

    def populate_from(self, source_dir: Path, ignore_patterns: Optional[list] = None) -> None:
        """Copy files from source_dir into the workspace with symlink containment checks."""
        if not self.path:
            raise RuntimeError("Workspace has not been entered.")

        source_dir = source_dir.resolve()
        ignore = shutil.ignore_patterns(*(ignore_patterns or [".git", ".venv", "__pycache__", ".pytest_cache"]))

        for item in source_dir.iterdir():
            dest = self.path / item.name

            # Security check on symlinks in source
            if item.is_symlink():
                try:
                    target = item.resolve(strict=True)
                    target.relative_to(source_dir)
                except (ValueError, FileNotFoundError):
                    logger.warning(f"Skipping dangerous source symlink escaping source root: {item}")
                    continue

            if item.is_dir():
                shutil.copytree(item, dest, ignore=ignore, symlinks=False)
            else:
                shutil.copy2(item, dest)

        # Check disk usage after population
        self.check_disk_usage()
        # Automatically record starting revision if not yet set
        if not self._starting_revision:
            self.record_starting_revision()

    def snapshot(self, snapshot_name: str = "initial") -> None:
        """Create an in-memory snapshot of current text files for rollback support."""
        if not self.path or not self.path.exists():
            return
        files_map: Dict[str, str] = {}
        for root, _, files in os.walk(self.path):
            for f in files:
                fp = Path(root) / f
                if not fp.is_symlink() and fp.stat().st_size < 500_000:
                    try:
                        rel = fp.relative_to(self.path).as_posix()
                        files_map[rel] = fp.read_text(encoding="utf-8")
                    except Exception:
                        pass
        self._snapshots[snapshot_name] = files_map

    def restore_snapshot(self, snapshot_name: str = "initial") -> None:
        """Restore workspace to a previously taken snapshot."""
        if not self.path or snapshot_name not in self._snapshots:
            return
        files_map = self._snapshots[snapshot_name]
        # Remove any files currently in workspace that weren't in snapshot
        for root, _, files in os.walk(self.path):
            for f in files:
                fp = Path(root) / f
                rel = fp.relative_to(self.path).as_posix()
                if rel not in files_map:
                    try:
                        fp.unlink()
                    except OSError:
                        pass
        # Restore snapshot contents
        for rel_path, content in files_map.items():
            dest = self.path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")


class WorkspaceManager:
    """Manages creation, tracking, concurrency enforcement, and cleanup of multiple isolated workspaces."""

    def __init__(self, base_dir: Optional[Path] = None, max_concurrent_tasks: int = 5):
        self.base_dir = Path(base_dir).resolve() if base_dir else Path(tempfile.gettempdir()) / "repopilot_workspaces"
        self.max_concurrent_tasks = max_concurrent_tasks
        self._active_workspaces: Dict[str, IsolatedWorkspace] = {}

    def create_workspace(
        self,
        task_id: str,
        max_disk_bytes: Optional[int] = None,
    ) -> IsolatedWorkspace:
        """Create and enter a new isolated workspace for a task, enforcing concurrency limits."""
        if len(self._active_workspaces) >= self.max_concurrent_tasks:
            raise ResourceLimitExceededError(
                f"Maximum concurrent task workspaces reached ({self.max_concurrent_tasks})."
            )

        ws = IsolatedWorkspace(
            base_dir=self.base_dir,
            prefix=f"task_{task_id}_",
            task_id=task_id,
            max_disk_bytes=max_disk_bytes,
        )
        ws.__enter__()
        self._active_workspaces[task_id] = ws
        return ws

    def release_workspace(self, task_id: str) -> None:
        """Clean up and release a specific task workspace."""
        if task_id in self._active_workspaces:
            ws = self._active_workspaces.pop(task_id)
            ws.cleanup()

    def cleanup_all(self) -> None:
        """Clean up all active workspaces."""
        for task_id in list(self._active_workspaces.keys()):
            self.release_workspace(task_id)
