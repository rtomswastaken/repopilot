"""Defensive security controls: path traversal defense, symlink isolation, and environment secret scrubbing."""

import fnmatch
import logging
import os
from pathlib import Path
import re
import sys
from typing import Dict, List, Optional, Set

from repopilot.core.exceptions import (
    PathTraversalError,
    ProhibitedPathError,
    SecurityViolationError,
    SymlinkEscapeError,
)

logger = logging.getLogger(__name__)

# Mandatory non-bypassable exclusions
BUILTIN_MANDATORY_EXCLUSIONS: List[str] = [
    ".github/workflows/",
    "secrets/",
    "production_credentials/",
    ".env",
    ".git/",
    ".ssh/",
    ".aws/",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
]

# Sensitive environment variable names that must never be exposed to untrusted code
EXPLICIT_SENSITIVE_ENV_VARS: Set[str] = {
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_ACTION",
    "GITHUB_ACTOR",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "MYSQL_PWD",
    "STRIPE_API_KEY",
    "SENDGRID_API_KEY",
}

# Substring patterns for heuristic secret detection in environment variables
SENSITIVE_KEYWORD_PATTERNS = [
    r".*TOKEN.*",
    r".*SECRET.*",
    r".*KEY.*",
    r".*PASSWORD.*",
    r".*CREDENTIAL.*",
    r".*AUTH.*",
    r".*PRIVATE.*",
]


class PathSecurityChecker:
    """Enforces strict path containment, symlink escape defense, and prohibited path policies."""

    def __init__(self, workspace_root: Path, excluded_patterns: Optional[List[str]] = None):
        self.workspace_root = workspace_root.resolve()
        patterns = list(BUILTIN_MANDATORY_EXCLUSIONS)
        if excluded_patterns:
            for p in excluded_patterns:
                if p not in patterns:
                    patterns.append(p)
        self.excluded_patterns = patterns

    def is_path_excluded(self, relative_path: str) -> bool:
        """Check if relative_path matches any built-in or user-configured excluded patterns."""
        norm_path = relative_path.replace("\\", "/").strip().lstrip("/")

        for pattern in self.excluded_patterns:
            pattern_norm = pattern.replace("\\", "/").strip().lstrip("/")
            # Directory prefix match (e.g. .github/workflows/ or secrets/)
            if pattern.endswith("/"):
                prefix = pattern_norm.rstrip("/")
                if norm_path.startswith(prefix + "/") or norm_path == prefix:
                    return True
            # Glob match
            if fnmatch.fnmatch(norm_path, pattern_norm) or fnmatch.fnmatch(norm_path, f"*{pattern_norm}*"):
                return True
            # Substring match for critical tokens (.env, secrets, workflows)
            if pattern_norm in norm_path.split("/"):
                return True
            if pattern_norm and pattern_norm in norm_path:
                return True

        return False

    def verify_no_symlink_escape(self, target_path: Path) -> None:
        """Verify that neither target_path nor any of its existing ancestor directories resolve outside workspace."""
        current = target_path
        # Check from target_path up to the highest existing directory
        while current != self.workspace_root and current != current.parent:
            if current.exists() or current.is_symlink():
                try:
                    real = current.resolve(strict=True)
                    real.relative_to(self.workspace_root)
                except (ValueError, FileNotFoundError):
                    raise SymlinkEscapeError(
                        f"Symlink escape detected: '{current}' resolves outside workspace to '{real if 'real' in locals() else 'external target'}'."
                    )
            current = current.parent

    def resolve_and_verify(self, raw_path: str) -> Path:
        """Thoroughly validate and resolve a relative file path within workspace root.
        
        Raises SecurityViolationError or subclasses if any check fails.
        """
        # Reject null bytes
        if "\0" in raw_path:
            raise SecurityViolationError("Null byte injection detected in path.")

        clean_path = raw_path.strip()
        if not clean_path:
            raise SecurityViolationError("Empty file path is prohibited.")

        # Reject absolute paths
        if clean_path.startswith("/") or clean_path.startswith("\\") or Path(clean_path).is_absolute():
            raise SecurityViolationError(f"Absolute or empty file paths are prohibited: '{raw_path}'")


        raw_target = self.workspace_root / clean_path

        # Check if any component along the un-resolved path is a symlink escaping workspace
        p = raw_target
        while p != self.workspace_root and p != p.parent:
            if p.is_symlink():
                try:
                    sym_target = p.resolve()
                    sym_target.relative_to(self.workspace_root)
                except ValueError:
                    raise SymlinkEscapeError(
                        f"Symlink escape detected: '{raw_path}' resolves outside workspace."
                    )
            p = p.parent

        # Resolve path
        resolved = raw_target.resolve()

        # Check path containment
        try:
            rel_to_root = resolved.relative_to(self.workspace_root).as_posix()
        except ValueError:
            raise PathTraversalError(f"Path traversal detected: '{raw_path}' attempts to escape workspace.")

        # Check symlink escapes on target
        self.verify_no_symlink_escape(resolved)


        # Check excluded paths
        if self.is_path_excluded(rel_to_root):
            raise ProhibitedPathError(
                f"Modifying path '{rel_to_root}' is forbidden. It matches an excluded path rule."
            )

        return resolved


class SecretSanitizer:
    """Sanitizes environment variables and file access before executing untrusted code."""

    @classmethod
    def is_sensitive_variable(cls, var_name: str) -> bool:
        """Check if an environment variable name is considered sensitive."""
        var_upper = var_name.upper()
        if var_upper in EXPLICIT_SENSITIVE_ENV_VARS:
            return True
        for pattern in SENSITIVE_KEYWORD_PATTERNS:
            if re.match(pattern, var_upper):
                return True
        return False

    @classmethod
    def sanitize_environment(
        cls,
        base_env: Optional[Dict[str, str]] = None,
        workspace_root: Optional[Path] = None,
        isolated_home: Optional[Path] = None,
    ) -> Dict[str, str]:
        """Produce a sanitized copy of environment variables stripped of all credentials."""
        source_env = base_env if base_env is not None else dict(os.environ)
        clean_env: Dict[str, str] = {}

        for k, v in source_env.items():
            if not cls.is_sensitive_variable(k):
                clean_env[k] = v

        # Always explicitly remove known secrets just in case
        for secret_key in EXPLICIT_SENSITIVE_ENV_VARS:
            clean_env.pop(secret_key, None)

        # Remove git and ssh credential helpers
        clean_env.pop("GIT_ASKPASS", None)
        clean_env.pop("SSH_AUTH_SOCK", None)
        clean_env.pop("SSH_ASKPASS", None)

        # Point HOME to an isolated ephemeral directory to prevent reading host credentials
        if isolated_home:
            clean_env["HOME"] = str(isolated_home)
        elif workspace_root:
            clean_env["HOME"] = str(workspace_root)

        # Ensure active python interpreter directory is at the head of PATH
        bin_dir = str(Path(sys.executable).parent)
        current_path = clean_env.get("PATH", "")
        clean_env["PATH"] = bin_dir + os.pathsep + current_path

        # If workspace_root provided, prepend to PYTHONPATH
        if workspace_root:
            current_pythonpath = clean_env.get("PYTHONPATH", "")
            clean_env["PYTHONPATH"] = str(workspace_root) + (os.pathsep + current_pythonpath if current_pythonpath else "")

        return clean_env
