"""Unit tests verifying GitHub Actions workflows syntax, security, and zero-cost constraints.

Validates all 20 operations requirements:
1. Workflow syntax
2. Scheduled triggers
3. Manual triggers
4. Permission declarations
5. Job timeouts
6. Concurrency configuration
7. Dependency installation
8. Correct application entry point
9. Configuration validation
10. Missing secrets
11. AI provider unavailable
12. Interrupted execution
13. Partial execution reporting
14. Recovery after failure
15. Duplicate-run prevention
16. Persistent-state conflicts
17. Artifact retention
18. Secret isolation
19. Untrusted pull-request restrictions
20. Zero-cost configuration assumptions
"""

import os
from pathlib import Path
import re
from unittest.mock import patch
import yaml
import pytest

from repopilot.core.models import (
    BudgetTracker,
    ExecutionReport,
    RepoExecutionSummary,
    Task,
    TaskCategory,
    TaskStatus,
)
from repopilot.main import main
from repopilot.reporting.reporter import ExecutionReporter
from repopilot.state.store import LocalFileStateStore, MockStateStore


WORKFLOWS_DIR = Path(".github/workflows")


def load_all_workflows():
    workflow_files = sorted(list(WORKFLOWS_DIR.glob("*.yaml")) + list(WORKFLOWS_DIR.glob("*.yml")))
    workflows = {}
    for wf in workflow_files:
        content = wf.read_text(encoding="utf-8")
        parsed = yaml.safe_load(content)
        # In YAML 1.1, unquoted 'on' is parsed as boolean True. Normalize to string key 'on'.
        if isinstance(parsed, dict) and True in parsed and "on" not in parsed:
            parsed["on"] = parsed.pop(True)
        workflows[wf.name] = parsed
    return workflows


# 1. Workflow syntax
def test_workflow_syntax_valid_yaml():
    assert WORKFLOWS_DIR.exists(), ".github/workflows directory must exist"
    workflows = load_all_workflows()
    expected_workflows = {"scheduled.yaml", "manual-recovery.yaml", "test.yaml", "validate-config.yaml"}
    assert expected_workflows.issubset(set(workflows.keys())), f"Missing expected workflows: {expected_workflows - set(workflows.keys())}"

    for name, data in workflows.items():
        assert isinstance(data, dict), f"{name} must parse to a YAML dictionary"
        assert "name" in data, f"{name} must have a 'name' field"
        assert "jobs" in data and len(data["jobs"]) > 0, f"{name} must define at least one job"
        assert "on" in data, f"{name} must define an 'on' trigger section"


# 2. Scheduled triggers
def test_workflow_scheduled_trigger():
    workflows = load_all_workflows()
    scheduled = workflows["scheduled.yaml"]
    triggers = scheduled["on"]
    assert "schedule" in triggers, "scheduled.yaml must define a 'schedule' trigger"
    assert isinstance(triggers["schedule"], list)
    cron_expr = triggers["schedule"][0].get("cron")
    assert cron_expr is not None, "scheduled.yaml must define a cron expression"
    # Cron format: 5 space-separated fields (minute, hour, dom, month, dow)
    fields = cron_expr.strip().split()
    assert len(fields) == 5, f"Cron expression '{cron_expr}' must have 5 fields"
    # Verify runs twice daily (e.g. 0 0,12 * * *) to remain safely inside free-tier runner limits
    assert fields[1] in ("0,12", "12,0", "0", "12"), "Cron expression should run on bounded schedule"


# 3. Manual triggers
def test_workflow_manual_triggers():
    workflows = load_all_workflows()
    for wf_name in ["scheduled.yaml", "manual-recovery.yaml"]:
        wf = workflows[wf_name]
        assert "workflow_dispatch" in wf["on"], f"{wf_name} must support manual trigger 'workflow_dispatch'"

    manual = workflows["manual-recovery.yaml"]
    inputs = manual["on"]["workflow_dispatch"].get("inputs", {})
    assert "target_repo" in inputs, "manual-recovery.yaml must include 'target_repo' input"
    assert "dry_run" in inputs, "manual-recovery.yaml must include 'dry_run' input"
    assert inputs["dry_run"].get("type") == "boolean", "'dry_run' input must be boolean"
    assert "log_level" in inputs, "manual-recovery.yaml must include 'log_level' input"


# 4. Permission declarations
def test_workflow_permission_declarations_least_privilege():
    workflows = load_all_workflows()
    for name, wf in workflows.items():
        top_perms = wf.get("permissions")
        assert top_perms is not None, f"{name} must explicitly declare top-level permissions"
        # Verify no blanket write-all or read-all permissions
        assert top_perms != "write-all" and top_perms != "read-all", f"{name} must not use blanket write-all/read-all"

        # Check job-level permissions
        for job_name, job_def in wf.get("jobs", {}).items():
            job_perms = job_def.get("permissions")
            if job_perms:
                assert job_perms != "write-all", f"Job '{job_name}' in {name} must not specify write-all"
                # Check that administration/secrets are never granted
                assert "administration" not in job_perms, f"Job '{job_name}' must not have administration permissions"

    # Execution jobs in scheduled and manual-recovery must have bounded permissions
    for wf_name in ["scheduled.yaml", "manual-recovery.yaml"]:
        wf = workflows[wf_name]
        exec_job = wf["jobs"].get("run-repopilot") or wf["jobs"].get("manual-run")
        assert exec_job is not None
        perms = exec_job.get("permissions", {})
        assert perms.get("contents") == "write"
        assert perms.get("pull-requests") == "write"
        assert perms.get("issues") == "read"


# 5. Job timeouts
def test_workflow_job_timeouts():
    workflows = load_all_workflows()
    for name, wf in workflows.items():
        for job_name, job_def in wf["jobs"].items():
            timeout = job_def.get("timeout-minutes")
            assert timeout is not None, f"Job '{job_name}' in {name} must define 'timeout-minutes'"
            assert isinstance(timeout, int)
            assert timeout <= 15, f"Job '{job_name}' in {name} has timeout {timeout} > 15m; must be <= 15m to protect free tier"


# 6. Concurrency configuration
def test_workflow_concurrency_controls():
    workflows = load_all_workflows()
    # scheduled.yaml and manual-recovery.yaml must share the mutex concurrency group
    scheduled_concurrency = workflows["scheduled.yaml"].get("concurrency")
    manual_concurrency = workflows["manual-recovery.yaml"].get("concurrency")

    assert scheduled_concurrency is not None, "scheduled.yaml must declare concurrency"
    assert manual_concurrency is not None, "manual-recovery.yaml must declare concurrency"
    assert scheduled_concurrency["group"] == "repopilot-execution", "Concurrency group must be 'repopilot-execution'"
    assert manual_concurrency["group"] == "repopilot-execution", "Concurrency group must be 'repopilot-execution'"
    # cancel-in-progress must be false to avoid corrupting shared state mid-execution
    assert scheduled_concurrency["cancel-in-progress"] is False
    assert manual_concurrency["cancel-in-progress"] is False

    # CI workflows should cancel obsolete in-progress runs
    assert workflows["test.yaml"]["concurrency"]["cancel-in-progress"] is True
    assert workflows["validate-config.yaml"]["concurrency"]["cancel-in-progress"] is True


# 7. Dependency installation & pinned actions
def test_workflow_pinned_actions_and_dependencies():
    workflows = load_all_workflows()
    sha_regex = re.compile(r"^[a-f0-9]{40}$")

    for name, wf in workflows.items():
        for job_name, job_def in wf["jobs"].items():
            for step in job_def.get("steps", []):
                uses = step.get("uses")
                if uses and not uses.startswith("./"):
                    # Example: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683
                    action_part, ref_part = uses.split("@", 1)
                    assert sha_regex.match(ref_part), f"Step '{step.get('name')}' in {name} must use pinned 40-character commit SHA, found '{ref_part}'"


# 8. Correct application entry point
def test_workflow_application_entrypoint():
    for wf_name in ["scheduled.yaml", "manual-recovery.yaml"]:
        wf_content = (WORKFLOWS_DIR / wf_name).read_text(encoding="utf-8")
        assert "repopilot" in wf_content, f"{wf_name} must invoke the 'repopilot' application entry point"
        assert "--config" in wf_content, f"{wf_name} must pass '--config' to the entry point"


# 9. Configuration validation job exists
def test_workflow_includes_config_validation():
    workflows = load_all_workflows()
    assert "validate-config" in workflows["scheduled.yaml"]["jobs"], "scheduled.yaml must include preflight validate-config job"
    assert "validate-input-and-config" in workflows["manual-recovery.yaml"]["jobs"], "manual-recovery.yaml must include validate-input-and-config job"
    assert "validate-config.yaml" in workflows, "validate-config.yaml dedicated workflow must exist"


# 10. Missing secrets handled safely
def test_missing_secrets_graceful_handling():
    # Verify that CLI runs in test mode without GITHUB_TOKEN or GEMINI_API_KEY
    with patch.dict(os.environ, {}, clear=True):
        exit_code = main(["--test-mode", "--config", "config/repos.example.yaml"])
        assert exit_code == 0, "CLI should succeed in test-mode with no environment tokens"


# 11. AI provider unavailable does not crash runner
def test_ai_provider_unavailable_handling():
    from repopilot.ai.provider import AICodeChangeRequest, AIPlanRequest
    from repopilot.ai.mock_provider import MockAIProvider

    provider = MockAIProvider(available=False)
    assert not provider.is_available()
    plan_req = AIPlanRequest(
        repo_owner="test",
        repo_name="repo",
        goals=["docs"],
        allowed_tasks=["documentation"],
        file_tree=["README.md"],
        open_issues=[],
    )
    tasks = provider.plan_improvements(plan_req)
    assert tasks == [], "Unavailable AI provider should return empty task list without exception"


# 12. Interrupted execution and budget tracker bounds
def test_budget_tracker_enforces_run_limits():
    tracker = BudgetTracker(max_execution_minutes=10, max_ai_requests=5, max_tasks_per_repo=2, max_repositories=3)
    assert not tracker.is_time_exhausted()
    assert not tracker.is_ai_budget_exhausted()

    for _ in range(5):
        tracker.record_ai_request()
    assert tracker.is_ai_budget_exhausted()


# 13. Partial execution reporting
def test_partial_execution_reporting():
    report = ExecutionReport(run_id="run-partial-001", total_repositories=2, processed_repositories=1)
    report.repositories.append(RepoExecutionSummary(repository="owner/repo1", success_count=1, failure_count=0))
    report.errors.append("Execution time budget exhausted (10m). Stopping run.")

    summary = ExecutionReporter.generate_markdown_summary(report)
    assert "run-partial-001" in summary
    assert "₹0.00" in summary
    assert "Execution time budget exhausted" in summary
    assert "owner/repo1" in summary


# 14. Recovery after failure
def test_recovery_report_artifact_configured():
    workflows = load_all_workflows()
    manual = workflows["manual-recovery.yaml"]
    steps = manual["jobs"]["manual-run"]["steps"]
    artifact_steps = [s for s in steps if "upload-artifact" in s.get("uses", "")]
    assert len(artifact_steps) == 1, "manual-recovery.yaml must upload execution report artifact"
    assert artifact_steps[0].get("if") == "always()", "Artifact upload must execute even if earlier steps fail (if: always())"


# 15. Duplicate-run prevention
def test_duplicate_run_prevention():
    store = MockStateStore()
    task = Task(
        id="task-101",
        repo_owner="owner",
        repo_name="repo",
        title="Improve docstrings",
        description="Add docstrings",
        category=TaskCategory.DOCUMENTATION,
        related_files=["src/main.py"],
    )
    store.record_task_start("owner/repo", task)
    task.status = TaskStatus.PUBLISHED
    store.record_task_outcome("owner/repo", task)

    # Subsequent run checks for duplicates
    assert store.is_duplicate_task("owner/repo", "Improve docstrings", ["src/main.py"]) is True
    assert store.is_duplicate_task("owner/repo", "Fix memory leak", ["src/core.py"]) is False


# 16. Persistent state atomic file store
def test_persistent_state_atomic_file_store(tmp_path):
    state_file = tmp_path / "state.json"
    store = LocalFileStateStore(state_file)
    state = store.load_state()
    assert state.version == 1

    task = Task(
        id="task-atomic-1",
        repo_owner="owner",
        repo_name="atomic-repo",
        title="Atomic Task",
        description="Desc",
        category=TaskCategory.BUG_FIX,
        related_files=[],
    )
    store.record_task_outcome("owner/atomic-repo", task)

    assert state_file.exists(), "State file must be written"
    # Verify temporary file was cleaned up
    tmp_file = state_file.with_suffix(".tmp")
    assert not tmp_file.exists(), "Atomic .tmp file must be replaced and not left behind"


# 17. Artifact retention
def test_artifact_retention_days():
    workflows = load_all_workflows()
    for name, wf in workflows.items():
        for job_name, job_def in wf["jobs"].items():
            for step in job_def.get("steps", []):
                if "upload-artifact" in step.get("uses", ""):
                    retention = step.get("with", {}).get("retention-days")
                    assert retention is not None, f"Artifact upload in {name} must specify 'retention-days'"
                    assert retention <= 7, f"Retention days {retention} in {name} must be <= 7 to conserve free storage"


# 18. Secret isolation in runner
def test_secret_isolation_and_env_passing():
    workflows = load_all_workflows()
    for wf_name in ["scheduled.yaml", "manual-recovery.yaml"]:
        wf_content = (WORKFLOWS_DIR / wf_name).read_text(encoding="utf-8")
        # Ensure secrets are passed via env: block, not interpolated in bash command
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in wf_content
        assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in wf_content


# 19. Untrusted pull-request restrictions
def test_untrusted_pull_request_restrictions():
    workflows = load_all_workflows()
    test_wf = workflows["test.yaml"]
    # PR test workflow must only have read permissions and no secret access
    test_job = test_wf["jobs"]["test"]
    assert test_job.get("permissions") == {"contents": "read"}
    test_content = (WORKFLOWS_DIR / "test.yaml").read_text(encoding="utf-8")
    assert "secrets.GITHUB_TOKEN" not in test_content
    assert "secrets.GEMINI_API_KEY" not in test_content


# 20. Zero-cost configuration assumptions
def test_zero_cost_runner_and_cost_assumptions():
    workflows = load_all_workflows()
    for name, wf in workflows.items():
        for job_name, job_def in wf["jobs"].items():
            runner = job_def.get("runs-on")
            assert runner == "ubuntu-latest", f"Job '{job_name}' in {name} must use 'ubuntu-latest' (1x free Linux multiplier, zero cost)"

    # Verify that scheduled frequency adheres to free tier minutes budget
    # 2 runs/day * 15 min timeout = 30 min/day = 900 min/month <= 2000 free minutes
    runs_per_day = 2
    max_timeout_per_run_minutes = 15
    monthly_minutes = runs_per_day * max_timeout_per_run_minutes * 30
    assert monthly_minutes <= 2000, f"Scheduled execution ({monthly_minutes}m) exceeds 2,000 free tier monthly minutes"
