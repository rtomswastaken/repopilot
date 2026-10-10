# RepoPilot: Operations & Deployment Guide

This guide describes how to deploy, configure, monitor, and recover RepoPilot with **₹0 operating cost** using GitHub Actions and free-tier AI inference.

---

## 1. Zero-Cost Operating Model

RepoPilot achieves ₹0 operating cost through deliberate architecture:

| Component | Standard Paid Tooling | RepoPilot Zero-Cost Tooling | Monthly Cost |
| :--- | :--- | :--- | :--- |
| **Compute** | VPS / Dedicated Server (\$10-20/mo) | GitHub Actions Free Runner (2,000 min/mo) | **₹0** |
| **AI Inference** | Paid OpenAI/Anthropic APIs (\$20-50/mo) | Google AI Studio Gemini Free Tier / Mock | **₹0** |
| **Storage / State** | Managed Postgres/Redis (\$15-30/mo) | Dedicated Git Branch (`repopilot-state`) | **₹0** |
| **Repository API** | Paid GitHub Enterprise APIs | Native GitHub Actions `GITHUB_TOKEN` | **₹0** |

---

## 2. GitHub Setup & Configuration

### A. Repository Secrets
Navigate to **Settings > Secrets and variables > Actions** in your RepoPilot repository:

1. `GITHUB_TOKEN`: Automatically provided by GitHub Actions for repositories within the same account.
   - For multi-account or organization management, create a fine-grained Personal Access Token (PAT) with `contents: write`, `pull-requests: write`, `issues: read` and save as `GH_TOKEN`.
2. `GEMINI_API_KEY` *(Optional)*:
   - Obtain a free API key from [Google AI Studio](https://aistudio.google.com).
   - Gemini 1.5 Flash provides free-of-charge API inference within standard rate limits.
   - If omitted, RepoPilot runs safely using rule-based/mock modes without failing.

### B. Configuring Managed Repositories
Create `config/repos.yaml` (copied from `config/repos.example.yaml`):

```yaml
version: 1
settings:
  max_repositories_per_run: 5
  max_tasks_per_repository: 2
  max_execution_minutes: 10
  max_ai_requests_per_run: 20
  ai_provider: gemini
  ai_model: gemini-1.5-flash

repositories:
  - owner: your-username
    name: your-project
    enabled: true
    goals:
      - improve_code_quality
      - fix_reproducible_bugs
      - improve_documentation
    allowed_tasks:
      - bug_fix
      - documentation
      - tests
    excluded_paths:
      - .github/workflows/
      - secrets/
      - production_credentials/
      - .env
    base_branch: main
    publish_mode: pull_request
    auto_merge: false
```

---

## 3. Workflow Execution

### A. Scheduled Runs
- Configured in `.github/workflows/scheduled.yaml`.
- Triggered automatically every 12 hours (`0 0,12 * * *`).
- Strict concurrency locks prevent overlapping jobs.
- Bounded 10-minute task budget prevents exhausting free runner quotas.

### B. Manual Recovery & Dry Runs
If a scheduled execution failed or you wish to test a newly added repository:

1. Go to **Actions > Manual Recovery Run > Run workflow**.
2. Select options:
   - `target_repo`: `owner/repository-name` (or leave blank to process all).
   - `dry_run`: `true` (validates plans without creating remote branches or PRs).
   - `log_level`: `INFO` or `DEBUG`.
3. Click **Run workflow**.

---

## 4. State Persistence & Diagnostics

### A. Persistent State Branch
RepoPilot maintains run state on an orphan branch named `repopilot-state`.
To view persistent state:
```bash
git fetch origin repopilot-state
git show origin/repopilot-state:state.json
```

### B. Resetting Persistent State
If state becomes stale or you want RepoPilot to re-evaluate all repositories from scratch:
```bash
git checkout --orphan repopilot-state
git rm -rf .
echo '{"version": 1, "repositories": {}}' > state.json
git add state.json
git commit -m "Reset persistent state"
git push origin repopilot-state --force
```

---

## 5. Troubleshooting & FAQ

### Q: Why did a task fail with "Command not found"?
**A:** The target repository configured custom test or lint commands (e.g. `ruff check .`) that are not installed on the base GitHub Actions runner. Either add the tools to `requirements.txt` or configure the validation command to run through a supported package manager.

### Q: What happens if Gemini API returns HTTP 429 (Rate Limit)?
**A:** RepoPilot catches rate limits, pauses with exponential backoff, and if persistent, gracefully skips remaining AI tasks for that repository. The failure is recorded in the run report without crashing the runner, and non-AI tasks continue.

### Q: Why are my PRs not automatically merging?
**A:** By design, `auto_merge` is disabled (`false`) by default. You must explicitly configure `auto_merge: true` in `config/repos.yaml` for that repository, and all validation checks must pass.
