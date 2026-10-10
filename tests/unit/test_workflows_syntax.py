"""Unit tests verifying GitHub Actions workflow syntax and security requirements."""

from pathlib import Path
import yaml


def test_github_workflows_valid_yaml():
    workflows_dir = Path(".github/workflows")
    assert workflows_dir.exists()

    workflow_files = list(workflows_dir.glob("*.yaml")) + list(workflows_dir.glob("*.yml"))
    assert len(workflow_files) >= 3

    for wf_path in workflow_files:
        content = wf_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, dict), f"{wf_path.name} is not a valid YAML dictionary"
        assert "name" in parsed
        assert "jobs" in parsed

        # Security check: every job must have timeout-minutes configured
        for job_name, job_def in parsed["jobs"].items():
            assert "timeout-minutes" in job_def, f"Job '{job_name}' in {wf_path.name} must specify timeout-minutes."
            assert job_def["timeout-minutes"] <= 30, f"Job '{job_name}' in {wf_path.name} timeout exceeds 30 minutes."
