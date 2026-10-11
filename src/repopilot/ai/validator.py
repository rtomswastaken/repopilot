"""Security and safety validation for AI proposals and generated patches."""

import fnmatch
import logging
from pathlib import Path
import re
from typing import List, Optional, Tuple

from repopilot.config.models import DEFAULT_MANDATORY_EXCLUDED_PATHS
from repopilot.core.exceptions import SecurityViolationError
from repopilot.core.models import (
    ChangeAction,
    ChangePlan,
    FileChange,
    Task,
    TaskCategory,
)

logger = logging.getLogger(__name__)

# Patterns indicative of test suppression or disabling
TEST_SUPPRESSION_PATTERNS = [
    re.compile(r"@pytest\.mark\.skip", re.IGNORECASE),
    re.compile(r"pytest\.skip\(", re.IGNORECASE),
    re.compile(r"unittest\.skip", re.IGNORECASE),
    re.compile(r"\bxit\(", re.IGNORECASE),
    re.compile(r"\btest\.skip\(", re.IGNORECASE),
    re.compile(r"\bit\.skip\(", re.IGNORECASE),
    re.compile(r"//\s*@ts-ignore", re.IGNORECASE),
    re.compile(r"#\s*noqa\b", re.IGNORECASE),
]

# Patterns indicative of malicious execution or privilege escalation
DANGEROUS_COMMAND_PATTERNS = [
    re.compile(r"\bcurl\s+.*\|\s*(?:bash|sh)\b", re.IGNORECASE),
    re.compile(r"\bwget\s+.*\|\s*(?:bash|sh)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+[/~]", re.IGNORECASE),
    re.compile(r"\bchmod\s+[0-7]*777\b", re.IGNORECASE),
    re.compile(r"\beval\(", re.IGNORECASE),
    re.compile(r"\bexec\(", re.IGNORECASE),
    re.compile(r"__import__\s*\(\s*['\"]os['\"]\s*\)\.system", re.IGNORECASE),
]

# Patterns for sensitive credentials or keys
SECRET_LEAK_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA|PGP)? PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9_]{36,255}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{36,255}"),
    re.compile(r"AIzaSy[A-Za-z0-9_\-]{33}"),
]


class SecurityProposalValidator:
    """Validates tasks and generated code changes against strict security constraints."""

    def __init__(
        self,
        excluded_paths: Optional[List[str]] = None,
        max_files_per_patch: int = 5,
        max_file_size_bytes: int = 100_000,
    ):
        self.excluded_paths = list(DEFAULT_MANDATORY_EXCLUDED_PATHS)
        if excluded_paths:
            for p in excluded_paths:
                if p not in self.excluded_paths:
                    self.excluded_paths.append(p)
        self.max_files_per_patch = max_files_per_patch
        self.max_file_size_bytes = max_file_size_bytes

    def is_path_excluded(self, relative_path: str) -> bool:
        """Check if relative_path matches any forbidden or excluded patterns."""
        norm_path = relative_path.replace("\\", "/").lstrip("/")

        for pattern in self.excluded_paths:
            pattern_norm = pattern.replace("\\", "/").rstrip("/")
            if pattern.endswith("/") and (norm_path.startswith(pattern_norm + "/") or norm_path == pattern_norm):
                return True
            if fnmatch.fnmatch(norm_path, pattern) or fnmatch.fnmatch(norm_path, f"*{pattern}*"):
                return True
            if pattern_norm in norm_path:
                return True
        return False

    def validate_task_proposal(self, task: Task, allowed_categories: Optional[List[str]] = None) -> Tuple[bool, str]:
        """Validate an autonomous task proposal before planning."""
        if not task.title or not task.title.strip():
            return False, "Task title cannot be empty."

        if not task.description or not task.description.strip():
            return False, "Task description cannot be empty."

        if allowed_categories:
            cat_val = task.category.value if isinstance(task.category, TaskCategory) else str(task.category)
            if cat_val not in allowed_categories:
                return False, f"Task category '{cat_val}' is not in allowed categories: {allowed_categories}"

        for rel_file in task.related_files:
            if not rel_file or Path(rel_file).is_absolute() or ".." in rel_file:
                return False, f"Invalid or traversing file path in task proposal: '{rel_file}'"
            if self.is_path_excluded(rel_file):
                return False, f"Task targets excluded or prohibited path: '{rel_file}'"

        return True, ""

    def validate_change_plan(self, plan: ChangePlan) -> Tuple[bool, str]:
        """Validate a generated ChangePlan against security bounds, path restrictions, and patch size."""
        if not plan.files:
            return False, "ChangePlan contains no file modifications."

        if len(plan.files) > self.max_files_per_patch:
            return False, f"ChangePlan exceeds maximum allowed files per patch ({len(plan.files)} > {self.max_files_per_patch})."

        for file_change in plan.files:
            raw_path = file_change.path.strip()
            if not raw_path or Path(raw_path).is_absolute():
                return False, f"Prohibited absolute or empty path: '{raw_path}'"

            # Path traversal check
            if ".." in Path(raw_path).parts or "\\" in raw_path:
                return False, f"Path traversal attempt detected in patch: '{raw_path}'"

            # Excluded path check
            if self.is_path_excluded(raw_path):
                return False, f"Change touches forbidden excluded path: '{raw_path}'"

            content = file_change.content or ""
            diff = file_change.diff or ""

            if len(content.encode("utf-8")) > self.max_file_size_bytes:
                return False, f"File content for '{raw_path}' exceeds maximum size budget ({self.max_file_size_bytes} bytes)."

            # Check for secrets or credential leakage
            for secret_pat in SECRET_LEAK_PATTERNS:
                if secret_pat.search(content) or secret_pat.search(diff):
                    return False, f"Proposed change in '{raw_path}' contains hardcoded credentials or secret keys."

            # Check for test disabling
            if "test" in raw_path.lower():
                for sup_pat in TEST_SUPPRESSION_PATTERNS:
                    if sup_pat.search(content):
                        return False, f"Proposed change in '{raw_path}' attempts to suppress or disable tests."

            # Check for dangerous shell execution
            for cmd_pat in DANGEROUS_COMMAND_PATTERNS:
                if cmd_pat.search(content):
                    return False, f"Proposed change in '{raw_path}' contains dangerous execution commands."

        return True, ""
