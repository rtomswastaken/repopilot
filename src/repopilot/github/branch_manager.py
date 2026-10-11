"""Safe Git branch name generation, sanitization, and lifecycle management.

Strictly complies with Git reference format rules (git-check-ref-format) and prevents
overwriting unrelated work or unauthorized force-pushes.
"""

import logging
import re
from typing import Optional, Tuple

from repopilot.core.exceptions import GitHubAPIError, SecurityViolationError

logger = logging.getLogger(__name__)

# Git ref restrictions: characters that can never appear in a branch name
FORBIDDEN_CHARS_REGEX = re.compile(r"""[\^~:?*\[\]\\@{}\s]""")


class BranchManager:
    """Manages Git branch naming, verification, sanitization, and ownership."""

    DEFAULT_PREFIX = "repopilot"

    @classmethod
    def sanitize_branch_name(cls, raw_name: str) -> str:
        """Sanitize an arbitrary string into a valid Git branch name conforming to git-check-ref-format.
        
        Rules enforced:
        1. Replace spaces, control characters, and ~^:?*[]\\@{ with hyphen.
        2. No consecutive dots ('..').
        3. No consecutive slashes ('//').
        4. No leading or trailing slashes or dots.
        5. Cannot end with '.lock'.
        6. Cannot contain ASCII control characters (0x00-0x1F, 0x7F).
        """
        if not raw_name:
            return "repopilot/task-branch"

        # 1. Remove control characters
        cleaned = "".join(ch for ch in raw_name if ord(ch) >= 32 and ord(ch) != 127)

        # 2. Replace forbidden chars with hyphen
        cleaned = FORBIDDEN_CHARS_REGEX.sub("-", cleaned)

        # 3. Collapse multiple consecutive dots to a single dot
        cleaned = re.sub(r"\.{2,}", ".", cleaned)

        # 4. Collapse multiple consecutive slashes
        cleaned = re.sub(r"/{2,}", "/", cleaned)

        # 5. Collapse multiple consecutive hyphens
        cleaned = re.sub(r"-{2,}", "-", cleaned)

        # 6. Strip leading and trailing slashes, dots, and hyphens from each component
        parts = cleaned.split("/")
        sanitized_parts = []
        for part in parts:
            p = part.strip(".-")
            if p:
                sanitized_parts.append(p)

        result = "/".join(sanitized_parts)

        # 7. Reject or fix '.lock' suffix
        if result.endswith(".lock"):
            result = result[:-5]

        # 8. Bound length to 80 characters
        if len(result) > 80:
            result = result[:80].rstrip(".-/")

        return result or "repopilot/task-branch"

    @classmethod
    def generate_branch_name(
        cls,
        task_id: str,
        title: str,
        prefix: str = DEFAULT_PREFIX,
        category: Optional[str] = None,
    ) -> str:
        """Generate a predictable, unique branch name for an autonomous task.
        
        Format: repopilot/<category>/<task-id>-<slug> or repopilot/<task-id>-<slug>
        """
        # Slugify title
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
        slug_short = slug[:30].rstrip("-")

        clean_task_id = re.sub(r"[^a-zA-Z0-9_\-]+", "", task_id)

        if category:
            clean_category = re.sub(r"[^a-zA-Z0-9_\-]+", "", category.lower())
            raw = f"{prefix}/{clean_category}/{clean_task_id}-{slug_short}"
        else:
            raw = f"{prefix}/{clean_task_id}-{slug_short}"

        return cls.sanitize_branch_name(raw)

    @classmethod
    def is_valid_branch_name(cls, branch_name: str) -> bool:
        """Validate if a branch name strictly complies with Git naming rules."""
        if not branch_name or branch_name.startswith("/") or branch_name.endswith("/"):
            return False
        if branch_name.startswith(".") or branch_name.endswith("."):
            return False
        if ".." in branch_name or "//" in branch_name or "@{ " in branch_name:
            return False
        if branch_name.endswith(".lock"):
            return False
        if FORBIDDEN_CHARS_REGEX.search(branch_name):
            return False
        return True

    @classmethod
    def is_repopilot_owned_branch(cls, branch_name: str, task_id: Optional[str] = None) -> bool:
        """Verify whether an existing branch is owned and managed by RepoPilot."""
        if not branch_name.startswith("repopilot/"):
            return False
        if task_id:
            clean_task_id = re.sub(r"[^a-zA-Z0-9_\-]+", "", task_id)
            return clean_task_id in branch_name
        return True

    @classmethod
    def detect_base_drift(cls, starting_base_sha: str, current_base_sha: str) -> bool:
        """Detect when the target base branch (e.g. main) has moved since task execution started."""
        if not starting_base_sha or not current_base_sha:
            return False
        return starting_base_sha != current_base_sha
