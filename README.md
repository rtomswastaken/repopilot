# RepoPilot

Autonomous GitHub repository management agent designed to run unattended on scheduled GitHub Actions workflows at zero infrastructure cost.

RepoPilot periodically inspects target repositories, analyzes code and issue context, plans targeted improvements (bug fixes, test coverage, documentation, type annotations), verifies changes in isolated workspaces, and opens pull requests with strict validation gates.

## Overview

RepoPilot is built to operate autonomously without requiring a self-hosted server or paid services. It executes on GitHub Actions free runners, persists task history directly on a Git orphan branch (`repopilot-state`), and uses Google Gemini free-tier inference (or an offline rule-based mock mode).

### Key Features

- Repository intelligence: Inspects codebase AST, detects frameworks and languages, and reads issues and documentation for task context.
- Task planning and repair: Generates structured patch proposals, detects repeated failed patches, diagnoses test failures, and enforces bounded retry limits.
- Isolated execution: Evaluates patches in temporary workspaces with transactional rollback snapshots on failure.
- Defensive execution checks: Blocks path traversal, symlink escapes, and modifications to forbidden files (`.github/workflows/`, secrets, `.env`).
- Environment scrubbing: Subprocess test runners strip sensitive environment variables (`GITHUB_TOKEN`, credentials, API keys) before running build or test commands.
- Publishing gates: Enforces 9 pre-commit verification checks and creates Git trees and commits through the GitHub API only after test suites pass.
- Merge policy engine: Evaluates 10 safety conditions before merging. Auto-merge is disabled by default and requires explicit configuration.
- Durable state without databases: Maintains task deduplication signatures, execution checkpoints, and retry counts on a Git orphan branch (`repopilot-state`).
- Offline non-AI mode: Fully operational without API keys using `--test-mode` or `--dry-run`.

## Tech Stack

- Python 3.10+
- Pydantic 2.0+ (configuration and data validation)
- PyYAML (configuration parsing)
- Pytest (test suite and test runner execution)
- GitHub REST API (branch, commit, pull request, and issue management)
- Google Gemini API (free tier) with pluggable provider architecture

## Requirements

- Python 3.10 or later
- Git 2.30 or later
- A GitHub repository with GitHub Actions enabled (for scheduled automation)
- Google Gemini API key (optional; required only for live AI planning)

## Installation

Clone the repository and install dependencies in a virtual environment:

```bash
git clone https://github.com/rtomswastaken/repopilot.git
cd repopilot

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

## Quick Start

### Run in Offline Test Mode

Execute a dry run using mock planners and zero external network calls:

```bash
repopilot --test-mode --config config/repos.example.yaml
```

Or using the Python module syntax:

```bash
python -m repopilot.main --test-mode --config config/repos.example.yaml
```

### Run with Live Gemini AI

Set the required environment variables and run against your configuration:

```bash
export GEMINI_API_KEY="your-gemini-api-key"
export GITHUB_TOKEN="your-github-token"

repopilot --config config/repos.yaml
```

### CLI Reference

| Flag | Description |
|---|---|
| `--config`, `-c` | Path to YAML configuration file (default: `config/repos.yaml` or `config/repos.example.yaml`). |
| `--test-mode` | Run in isolated local mode with mock providers and zero external side effects. |
| `--dry-run` | Alias for `--test-mode`. |
| `--repo` | Target a single repository by identifier (e.g. `owner/repo`). |
| `--json-logs` | Emit structured JSON log lines instead of formatted text. |
| `--log-level` | Log verbosity level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`). |
| `--report-out` | Optional path to write the JSON execution summary report. |

## Configuration

RepoPilot is configured via a YAML file (e.g. `config/repos.yaml`).

```yaml
version: 1

settings:
  # Limits per scheduled run
  max_repositories_per_run: 5
  max_tasks_per_repository: 2
  max_retries_per_task: 2
  max_execution_minutes: 10
  max_ai_requests_per_run: 20

  # Validation and publishing gates
  require_validation: true
  default_publish_mode: pull_request  # Options: pull_request, draft_pr, branch_only

  # State persistence (Git orphan branch avoids external database costs)
  state_backend: git_branch           # Options: git_branch, local_file, mock
  state_branch_name: repopilot-state
  state_file_path: .repopilot/state.json

  # AI Provider settings
  ai_provider: gemini                 # Options: gemini, mock, none
  ai_model: gemini-1.5-flash
  ai_request_timeout_seconds: 30

repositories:
  - owner: YOUR_GITHUB_USERNAME
    name: YOUR_REPOSITORY
    enabled: true

    goals:
      - improve_code_quality
      - fix_reproducible_bugs
      - improve_documentation
      - increase_test_coverage

    allowed_tasks:
      - bug_fix
      - documentation
      - tests
      - code_quality
      - type_annotation

    # Paths that RepoPilot will refuse to touch
    excluded_paths:
      - .github/workflows/
      - secrets/
      - production_credentials/
      - "*.pem"
      - "*.key"
      - .env

    base_branch: main

    validation:
      test_command: "pytest tests/"
      lint_command: "ruff check ."
      timeout_seconds: 180

    publish_mode: pull_request
    auto_merge: false
```

## GitHub Actions Deployment

### 1. Workflow Permissions

In your GitHub repository, go to **Settings > Actions > General > Workflow permissions**:
- Select **Read and write permissions**.
- Check **Allow GitHub Actions to create and approve pull requests**.

### 2. Secrets

Under **Settings > Secrets and variables > Actions**, add:
- `GEMINI_API_KEY`: Google Gemini API key from Google AI Studio.
- `GITHUB_TOKEN`: Provided automatically by GitHub Actions. (For managing separate external repositories, configure a personal access token named `GH_TOKEN`).

### 3. Workflows

RepoPilot includes four predefined workflows in `.github/workflows/`:

- `scheduled.yaml`: Runs twice daily (`0 0,12 * * *`) on a cron schedule to execute pending tasks within free-tier runner limits.
- `manual-recovery.yaml`: Manual trigger (`workflow_dispatch`) for on-demand execution, specific repository targeting, or dry runs.
- `test.yaml`: CI test matrix running unit and integration tests on pull requests and pushes to `main`.
- `validate-config.yaml`: Validates configuration syntax against Pydantic schemas before running jobs.

## Development and Testing

Run the full test suite with Pytest:

```bash
pytest -v
```

Run test suite with coverage report:

```bash
pytest --cov=repopilot --cov-report=term-missing
```

The test suite covers:
- Configuration parsing, Pydantic validation, and defaults
- Subprocess execution, timeouts, and credential scrubbing
- Path traversal and forbidden path enforcement
- AST inspection and framework detection
- Repair planner heuristics and anti-suppression checks
- Git blob, tree, commit, and pull request publication pipelines
- Git orphan branch state storage, atomic updates, and recovery
- Workflow YAML schema and security checks

## Status and Limitations

- All 140 unit and integration tests pass locally and in CI.
- Subprocess validation uses isolated temporary working directories and environment variable stripping. It does not use Linux kernel cgroups or container virtualization. When running untrusted code from public forks, run RepoPilot in an isolated container or runner environment.
- Google Gemini free tier rate limits (15 RPM / 1,500 RPD) are respected via an internal budget tracker that caps AI calls per run. When rate limits or quotas are reached, execution gracefully falls back or halts until the next scheduled run.

## License

This project is licensed under the Apache License 2.0. See `pyproject.toml` for metadata.
