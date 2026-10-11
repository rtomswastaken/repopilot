"""Security policy enforcement, sensitive file detection, and secret scanning."""

import fnmatch
import logging
import re
from typing import List, Optional, Tuple

from repopilot.core.models import ChangePlan, FileChange
from repopilot.github.models import SecretScanMatch, SecurityAuditResult

logger = logging.getLogger(__name__)

# Sensitive path categories requiring extra scrutiny
SENSITIVE_PATTERNS = {
    "auth_and_authorization": [
        "*auth*", "*login*", "*oauth*", "*jwt*", "*permission*", "*rbac*",
        "*access_control*", "*session_auth*",
    ],
    "credential_handling": [
        "*secret*", "*password*", "*.env*", "*credential*", "*api_key*",
        "*token_storage*", "*id_rsa*", "*id_ed25519*",
    ],
    "security_policies": [
        "SECURITY.md", ".github/SECURITY.md", ".github/security*",
        "*security_policy*", "*firewall*", "*csp*",
    ],
    "privileged_workflows": [
        ".github/workflows/*", ".github/actions/*", ".gitlab-ci.yml",
    ],
    "deployment_permissions": [
        "*deploy*", "*terraform*", "*k8s*", "*kubernetes*", "*helm*",
        "Dockerfile*", "docker-compose*.yml", "docker-compose*.yaml",
    ],
    "secret_management": [
        "*vault*", "*kms*", "*keychain*", "*secrets_manager*",
    ],
    "repository_protection": [
        "CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
    ],
    "dependency_trust_controls": [
        "setup.py", "setup.cfg", "package.json", "requirements*.txt",
        "pyproject.toml", "Cargo.toml", "go.mod", "pom.xml",
    ],
}

# Regex patterns for finding accidental secrets in code/diffs
SECRET_PATTERNS: List[Tuple[str, str]] = [
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("github_pat_classic", r"ghp_[a-zA-Z0-9]{36}"),
    ("github_fine_grained_pat", r"github_pat_[a-zA-Z0-9_]{82}"),
    ("github_app_token", r"ghs_[a-zA-Z0-9]{36}"),
    ("aws_access_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("slack_token", r"xox[baprs]-[0-9a-zA-Z]{10,48}"),
    (
        "generic_hardcoded_credential",
        r"""(?i)(?:api_key|access_token|secret_key|client_secret|auth_token)\s*[:=]\s*["'][a-zA-Z0-9_\-\+/]{20,}["']""",
    ),
]

DESTRUCTIVE_SQL_PATTERN = re.compile(
    r"""(?i)\b(?:DROP\s+TABLE|DROP\s+DATABASE|DROP\s+SCHEMA|TRUNCATE\s+TABLE|ALTER\s+TABLE\s+\w+\s+DROP\s+COLUMN)\b"""
)


class SecurityPolicyChecker:
    """Evaluates proposed changes against repository security policies and secret leakage rules."""

    @classmethod
    def scan_content_for_secrets(cls, content: str, file_path: str = "") -> List[SecretScanMatch]:
        """Scan file content or diff for hardcoded API keys and secrets."""
        matches: List[SecretScanMatch] = []
        if not content:
            return matches

        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            for secret_type, regex in SECRET_PATTERNS:
                if re.search(regex, line):
                    masked_snippet = re.sub(regex, "[REDACTED_SECRET]", line)
                    matches.append(
                        SecretScanMatch(
                            file_path=file_path,
                            secret_type=secret_type,
                            line_number=idx,
                            snippet=masked_snippet.strip(),
                        )
                    )
        return matches

    @classmethod
    def is_destructive_migration(cls, file_path: str, content: str) -> bool:
        """Check if a database migration file executes destructive schema drops."""
        is_migration = any(kw in file_path.lower() for kw in ["migration", "alembic", "flyway", "liquibase", ".sql"])
        if is_migration and content:
            if DESTRUCTIVE_SQL_PATTERN.search(content):
                return True
        return False

    @classmethod
    def check_sensitive_areas(cls, relative_path: str) -> List[str]:
        """Identify which security-sensitive categories match a file path."""
        norm_path = relative_path.replace("\\", "/").lstrip("/")
        detected: List[str] = []

        for category, patterns in SENSITIVE_PATTERNS.items():
            for pattern in patterns:
                pattern_norm = pattern.replace("\\", "/").rstrip("/")
                if pattern.endswith("/*"):
                    prefix = pattern_norm[:-2]
                    if norm_path.startswith(prefix + "/") or norm_path == prefix:
                        detected.append(category)
                        break
                elif fnmatch.fnmatch(norm_path, pattern) or fnmatch.fnmatch(norm_path.split("/")[-1], pattern):
                    detected.append(category)
                    break
                elif pattern_norm in norm_path:
                    detected.append(category)
                    break

        return detected

    @classmethod
    def audit_change_plan(
        cls,
        plan: ChangePlan,
        excluded_paths: Optional[List[str]] = None,
    ) -> SecurityAuditResult:
        """Thoroughly audit a proposed ChangePlan for:
        1. Explicitly excluded path violations
        2. Security-sensitive area modifications
        3. Hardcoded secrets or tokens
        4. Destructive migrations
        """
        violations: List[str] = []
        found_secrets: List[SecretScanMatch] = []
        sensitive_areas: List[str] = []

        excluded = excluded_paths or []

        for change in plan.files:
            file_p = change.path.strip()

            # 1. Excluded path check
            for exc in excluded:
                exc_norm = exc.replace("\\", "/").rstrip("/")
                norm_p = file_p.replace("\\", "/").lstrip("/")
                if exc.endswith("/") and (norm_p.startswith(exc_norm + "/") or norm_p == exc_norm):
                    violations.append(f"Modifying path '{file_p}' violates excluded path rule '{exc}'.")
                elif exc_norm in norm_p:
                    violations.append(f"Modifying path '{file_p}' violates excluded path rule '{exc}'.")

            # 2. Check sensitive areas
            areas = cls.check_sensitive_areas(file_p)
            for a in areas:
                if a not in sensitive_areas:
                    sensitive_areas.append(a)

            # 3. Secret scanning on content and diff
            target_content = (change.content or "") + "\n" + (change.diff or "")
            secrets = cls.scan_content_for_secrets(target_content, file_path=file_p)
            found_secrets.extend(secrets)

            # 4. Destructive migration check
            if cls.is_destructive_migration(file_p, target_content):
                violations.append(f"Destructive database migration detected in '{file_p}'.")

        if found_secrets:
            for sec in found_secrets:
                violations.append(f"Potential secret detected ({sec.secret_type}) in '{sec.file_path}' (line {sec.line_number}).")

        is_safe = len(violations) == 0

        return SecurityAuditResult(
            is_safe=is_safe,
            violations=violations,
            found_secrets=found_secrets,
            sensitive_areas=sensitive_areas,
        )
