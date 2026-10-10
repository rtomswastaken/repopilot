"""Isolated workspace management for safe code execution and patch application."""

import logging
import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class IsolatedWorkspace:
    """Provides a temporary, isolated workspace directory.

    Guarantees automatic cleanup upon exit.
    """

    def __init__(self, base_dir: Optional[Path] = None, prefix: str = "repopilot_ws_"):
        self.base_dir = base_dir
        self.prefix = prefix
        self.path: Optional[Path] = None

    def __enter__(self) -> "IsolatedWorkspace":
        temp_dir = tempfile.mkdtemp(prefix=self.prefix, dir=self.base_dir)
        self.path = Path(temp_dir).resolve()
        logger.debug(f"Created isolated workspace at: {self.path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.path and self.path.exists():
            try:
                shutil.rmtree(self.path, ignore_errors=True)
                logger.debug(f"Cleaned up isolated workspace at: {self.path}")
            except Exception as e:
                logger.warning(f"Failed to remove temporary workspace {self.path}: {e}")
        self.path = None

    def populate_from(self, source_dir: Path, ignore_patterns: Optional[list] = None) -> None:
        """Copy files from source_dir into the workspace."""
        if not self.path:
            raise RuntimeError("Workspace has not been entered.")

        ignore = shutil.ignore_patterns(*(ignore_patterns or [".git", ".venv", "__pycache__"]))
        for item in source_dir.iterdir():
            dest = self.path / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=ignore, symlinks=False)
            else:
                shutil.copy2(item, dest)
