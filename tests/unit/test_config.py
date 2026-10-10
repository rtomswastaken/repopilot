"""Unit tests for configuration loading, schema validation, and security constraints."""

import pytest
from pydantic import ValidationError

from repopilot.config.loader import load_config
from repopilot.config.models import (
    DEFAULT_MANDATORY_EXCLUDED_PATHS,
    GlobalSettings,
    RepoPilotConfig,
    RepositoryConfig,
)
from repopilot.core.exceptions import ConfigurationError
from repopilot.core.models import PublishMode, TaskCategory


def test_load_example_config():
    """Verify that the provided config/repos.example.yaml loads and validates cleanly."""
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

    # Check mandatory excluded paths are present
    for req in DEFAULT_MANDATORY_EXCLUDED_PATHS:
        assert any(req in p for p in repo.excluded_paths)


def test_reject_invalid_version():
    """Schema must reject versions other than 1."""
    with pytest.raises(ValidationError):
        RepoPilotConfig(version=2)


def test_reject_negative_or_extreme_limits():
    """Execution limits must be strictly bounded to prevent infinite loops or cost overruns."""
    with pytest.raises(ValidationError):
        GlobalSettings(max_execution_minutes=0)

    with pytest.raises(ValidationError):
        GlobalSettings(max_execution_minutes=120)  # Exceeds max 60 min

    with pytest.raises(ValidationError):
        GlobalSettings(max_repositories_per_run=-1)


def test_auto_merge_defaults_to_false():
    """Auto-merge must strictly default to False for all repositories."""
    repo = RepositoryConfig(owner="test-org", name="test-repo")
    assert repo.auto_merge is False


def test_mandatory_excluded_paths_cannot_be_cleared():
    """Even if a config specifies empty excluded_paths, critical paths must be retained."""
    repo = RepositoryConfig(owner="test-org", name="test-repo", excluded_paths=[])
    for req in DEFAULT_MANDATORY_EXCLUDED_PATHS:
        assert any(req in p for p in repo.excluded_paths)


def test_reject_invalid_owner_or_repo_name():
    """Owner and name must not contain path traversal or slashes."""
    with pytest.raises(ValidationError):
        RepositoryConfig(owner="owner/slash", name="repo")

    with pytest.raises(ValidationError):
        RepositoryConfig(owner="owner", name="repo/slash")


def test_load_config_missing_file():
    """Loading a non-existent configuration file must raise ConfigurationError."""
    with pytest.raises(ConfigurationError):
        load_config("non_existent_config_file_12345.yaml")
