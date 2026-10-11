"""Unit tests for configuration loading, schema validation, versioning, and security constraints."""

import tempfile
from pathlib import Path
import pytest
from pydantic import ValidationError
import yaml

from repopilot.config.loader import load_config
from repopilot.config.models import (
    DEFAULT_MANDATORY_EXCLUDED_PATHS,
    GlobalSettings,
    RepoPilotConfig,
    RepositoryConfig,
    ValidationSettings,
)
from repopilot.core.exceptions import ConfigurationError
from repopilot.core.models import PublishMode, TaskCategory


def test_valid_configuration_loading():
    """Requirement 1: Valid configuration loading across all schema sections."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "repos.yaml"
        sample_config = {
            "version": 1,
            "settings": {
                "max_repositories_per_run": 5,
                "max_tasks_per_repository": 3,
                "max_retries_per_task": 2,
                "max_execution_minutes": 15,
                "max_ai_requests_per_run": 30,
                "require_validation": True,
                "default_publish_mode": "pull_request",
                "state_backend": "local_file",
                "state_branch_name": "custom-state",
                "state_file_path": ".repopilot/custom.json",
                "ai_provider": "mock",
                "ai_model": "gemini-1.5-flash",
                "ai_request_timeout_seconds": 45,
                "reporting": {
                    "step_summary_enabled": True,
                    "issue_reporting_enabled": False,
                    "retention_days": 14,
                },
            },
            "repositories": [
                {
                    "owner": "org",
                    "name": "repo-a",
                    "enabled": True,
                    "base_branch": "develop",
                    "goals": ["fix_bugs", "improve_docs"],
                    "allowed_tasks": ["bug_fix", "documentation"],
                    "excluded_paths": [".github/workflows/", "secrets/"],
                    "validation": {
                        "test_command": "pytest",
                        "lint_command": "flake8",
                        "timeout_seconds": 120,
                    },
                    "publish_mode": "pull_request",
                    "auto_merge": False,
                }
            ],
        }
        config_path.write_text(yaml.dump(sample_config), encoding="utf-8")

        config = load_config(config_path)
        assert config.version == 1
        assert config.settings.max_repositories_per_run == 5
        assert config.settings.max_tasks_per_repository == 3
        assert config.settings.max_execution_minutes == 15
        assert config.settings.reporting.retention_days == 14
        assert len(config.repositories) == 1
        repo = config.repositories[0]
        assert repo.owner == "org"
        assert repo.name == "repo-a"
        assert repo.base_branch == "develop"
        assert repo.auto_merge is False
        assert repo.validation.test_command == "pytest"
        assert repo.validation.lint_command == "flake8"


def test_invalid_configuration_rejection():
    """Requirement 2: Reject invalid configurations with informative error messages."""
    # Test invalid YAML syntax
    with tempfile.TemporaryDirectory() as tmp_dir:
        invalid_yaml_file = Path(tmp_dir) / "invalid.yaml"
        invalid_yaml_file.write_text("settings:\n  max_execution_minutes: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(invalid_yaml_file)
        assert "YAML parsing error" in str(exc_info.value)

    # Test non-dict YAML root
    with tempfile.TemporaryDirectory() as tmp_dir:
        list_yaml_file = Path(tmp_dir) / "list.yaml"
        list_yaml_file.write_text("- item1\n- item2", encoding="utf-8")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(list_yaml_file)
        assert "root must be a YAML dictionary" in str(exc_info.value)

    # Test out-of-bound limits
    with pytest.raises(ValidationError):
        GlobalSettings(max_execution_minutes=0)

    with pytest.raises(ValidationError):
        GlobalSettings(max_execution_minutes=100)

    with pytest.raises(ValidationError):
        GlobalSettings(max_repositories_per_run=0)

    # Test invalid owner / name formatting
    with pytest.raises(ValidationError):
        RepositoryConfig(owner="owner/slash", name="repo")

    with pytest.raises(ValidationError):
        RepositoryConfig(owner="owner", name="repo\\slash")


def test_missing_optional_settings():
    """Requirement 3: Missing optional settings receive predictable and safe defaults."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        minimal_file = Path(tmp_dir) / "minimal.yaml"
        minimal_data = {
            "version": 1,
            "repositories": [
                {
                    "owner": "test-org",
                    "name": "test-app",
                }
            ],
        }
        minimal_file.write_text(yaml.dump(minimal_data), encoding="utf-8")
        config = load_config(minimal_file)

        # Global defaults applied
        assert config.settings.max_repositories_per_run == 10
        assert config.settings.max_tasks_per_repository == 2
        assert config.settings.max_execution_minutes == 10
        assert config.settings.max_retries_per_task == 2
        assert config.settings.require_validation is True
        assert config.settings.reporting.step_summary_enabled is True
        assert config.settings.reporting.issue_reporting_enabled is False

        # Repository defaults applied
        repo = config.repositories[0]
        assert repo.enabled is True
        assert repo.base_branch == "main"
        assert repo.auto_merge is False
        assert repo.publish_mode == PublishMode.PULL_REQUEST
        assert len(repo.goals) > 0
        assert len(repo.allowed_tasks) > 0
        # Mandatory excluded paths preserved
        for req in DEFAULT_MANDATORY_EXCLUDED_PATHS:
            assert any(req in p for p in repo.excluded_paths)


def test_unsafe_policy_rejection():
    """Requirement 4: Unsafe policies and secrets in config are strictly rejected."""
    # Rejection of credentials in configuration
    with tempfile.TemporaryDirectory() as tmp_dir:
        secret_file = Path(tmp_dir) / "secrets_config.yaml"
        secret_data = {
            "version": 1,
            "settings": {
                "ai_provider": "gemini",
            },
            "api_key": "AIzaSyFakeKey1234567890",
            "repositories": [{"owner": "org", "name": "repo"}],
        }
        secret_file.write_text(yaml.dump(secret_data), encoding="utf-8")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(secret_file)
        assert "Credential setting 'api_key' found" in str(exc_info.value)

    # Rejection of GitHub token in repo config
    with tempfile.TemporaryDirectory() as tmp_dir:
        token_file = Path(tmp_dir) / "token_config.yaml"
        token_data = {
            "version": 1,
            "repositories": [{"owner": "org", "name": "repo", "token": "ghp_fake123"}],
        }
        token_file.write_text(yaml.dump(token_data), encoding="utf-8")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(token_file)
        assert "Credentials must never be stored in configuration files" in str(exc_info.value)

    # Rejection of auto_merge with empty allowed_tasks
    with pytest.raises(ValidationError):
        RepositoryConfig(
            owner="org",
            name="repo",
            auto_merge=True,
            allowed_tasks=[],
        )

    # Clearing mandatory excluded paths is prevented
    repo = RepositoryConfig(owner="org", name="repo", excluded_paths=[])
    for req in DEFAULT_MANDATORY_EXCLUDED_PATHS:
        assert any(req in p for p in repo.excluded_paths)


def test_configuration_version_compatibility():
    """Requirement 5: Support explicit versioning and reject unsupported versions."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        v2_file = Path(tmp_dir) / "v2.yaml"
        v2_file.write_text("version: 2\nrepositories: []", encoding="utf-8")
        with pytest.raises(ConfigurationError) as exc_info:
            load_config(v2_file)
        assert "Unsupported configuration version: 2" in str(exc_info.value)

    with pytest.raises(ValidationError):
        RepoPilotConfig(version=0)

    with pytest.raises(ValidationError):
        RepoPilotConfig(version=99)


def test_load_example_config():
    """Verify that config/repos.example.yaml loads and validates cleanly."""
    config = load_config("config/repos.example.yaml")
    assert config.version == 1
    assert config.settings.max_repositories_per_run == 10
    assert config.settings.max_execution_minutes == 10
    assert len(config.repositories) == 1

    repo = config.repositories[0]
    assert repo.owner == "YOUR_GITHUB_USERNAME"
    assert repo.name == "YOUR_REPOSITORY"
    assert repo.auto_merge is False
    assert repo.publish_mode == PublishMode.PULL_REQUEST

    for req in DEFAULT_MANDATORY_EXCLUDED_PATHS:
        assert any(req in p for p in repo.excluded_paths)


def test_load_config_missing_file():
    """Loading a non-existent configuration file must raise ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config("non_existent_config_file_12345.yaml")
