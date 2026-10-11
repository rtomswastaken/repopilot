"""GitHub authentication and credential management.

Supports:
- Least-privilege Personal Access Tokens (classic and fine-grained PAT)
- GitHub Actions ephemeral GITHUB_TOKEN
- GitHub App installation access tokens

Security Invariants:
- Never hardcode credentials.
- Never log or print unmasked tokens.
- Never persist credentials in task state.
- Gracefully handle expired tokens and insufficient permissions.
"""

from datetime import datetime, timezone
from enum import Enum
import logging
import os
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from repopilot.core.exceptions import GitHubAPIError

logger = logging.getLogger(__name__)

# Exact GitHub Permissions Required per Operation
OPERATION_PERMISSIONS: Dict[str, Dict[str, str]] = {
    "read_repository_contents": {
        "contents": "read",
        "metadata": "read",
    },
    "create_branch": {
        "contents": "write",
    },
    "create_commit": {
        "contents": "write",
    },
    "push_changes": {
        "contents": "write",
    },
    "manage_pull_requests": {
        "pull_requests": "write",
    },
    "read_status_checks": {
        "checks": "read",
        "statuses": "read",
    },
    "merge_pull_request": {
        "contents": "write",
        "pull_requests": "write",
    },
}


def redact_token(token: Optional[str]) -> str:
    """Mask a token for safe logging and serialization.
    
    Examples:
        ghp_1234567890abcdef -> ghp_***cdef
        short_token -> ***
    """
    if not token:
        return "[NOT SET]"
    if len(token) <= 8:
        return "***"
    prefix = token[:4]
    suffix = token[-4:]
    return f"{prefix}***{suffix}"


class AuthMethod(str, Enum):
    TOKEN = "token"  # PAT or GITHUB_TOKEN
    GITHUB_APP = "github_app"  # GitHub App installation token
    ANONYMOUS = "anonymous"  # Read-only public API (rate-limited)


class GitHubCredentials:
    """Secure credential holder that enforces zero leakage in logs or string representations."""

    def __init__(
        self,
        token: Optional[str] = None,
        auth_method: AuthMethod = AuthMethod.TOKEN,
        expires_at: Optional[datetime] = None,
        app_id: Optional[str] = None,
        installation_id: Optional[str] = None,
        permissions: Optional[Dict[str, str]] = None,
    ):
        self._token = token
        self.auth_method = auth_method
        self.expires_at = expires_at
        self.app_id = app_id
        self.installation_id = installation_id
        self.permissions = permissions or {}

    @classmethod
    def from_environment(cls) -> "GitHubCredentials":
        """Load credentials from environment variables in order of precedence:
        1. REPOPILOT_GITHUB_APP_TOKEN
        2. GITHUB_TOKEN
        3. GH_TOKEN
        """
        app_token = os.environ.get("REPOPILOT_GITHUB_APP_TOKEN")
        if app_token:
            return cls(token=app_token, auth_method=AuthMethod.GITHUB_APP)

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            return cls(token=token, auth_method=AuthMethod.TOKEN)

        return cls(token=None, auth_method=AuthMethod.ANONYMOUS)

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def get_raw_token(self) -> Optional[str]:
        """Obtain raw token solely for HTTP authorization header creation."""
        if self.is_expired():
            raise GitHubAPIError("GitHub authentication token has expired. Re-authentication required.")
        return self._token

    def is_expired(self) -> bool:
        """Check if credential has passed its expiration time."""
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        target = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        return now >= target

    def get_auth_header(self) -> Dict[str, str]:
        """Generate Authorization header or empty dict if anonymous."""
        token = self.get_raw_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def verify_permission_for(self, operation: str, repo_permissions: Optional[Dict[str, bool]] = None) -> bool:
        """Verify whether credentials or repo permissions permit an operation.
        
        Args:
            operation: Name of operation in OPERATION_PERMISSIONS.
            repo_permissions: Permission dict from repository metadata (e.g. {'push': True, 'pull': True, 'admin': False}).
        """
        required = OPERATION_PERMISSIONS.get(operation, {})
        if not required:
            return True

        if repo_permissions:
            # GitHub repo permissions typically contain 'admin', 'maintain', 'push', 'triage', 'pull'
            can_push = repo_permissions.get("push", False) or repo_permissions.get("admin", False)
            if any(lvl == "write" for lvl in required.values()) and not can_push:
                return False

        return True

    def __repr__(self) -> str:
        masked = redact_token(self._token)
        return f"<GitHubCredentials method={self.auth_method.value} token={masked} expired={self.is_expired()}>"

    def __str__(self) -> str:
        return self.__repr__()
