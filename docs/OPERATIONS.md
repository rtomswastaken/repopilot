# ✈️ RepoPilot: Operations & Deployment Guide

> **Production manual for autonomous, zero-cost (₹0) repository operations on GitHub Actions.**

RepoPilot is designed to run completely unattended on GitHub-hosted runners. Once deployed, the system does not require the repository owner's computer to be powered on, relies on no paid infrastructure, and strictly operates within free allowances.

---

## 1. Operating Model & Zero-Cost Architecture

RepoPilot enforces a **₹0 operating cost target** by leveraging free-tier developer infrastructure:

| Subsystem | Conventional Commercial Architecture | RepoPilot Zero-Cost Architecture | Monthly Cost |
| :--- | :--- | :--- | :--- |
| **Compute Engine** | Dedicated cloud VM / Kubernetes cluster (\$15–\$60/mo) | Ephemeral GitHub Actions Ubuntu runners (`ubuntu-latest`) | **₹0** (within free tier) |
| **AI Inference** | Paid OpenAI / Anthropic API subscriptions (\$20–\$100/mo) | Google Gemini 1.5/2.0 Flash (Google AI Studio Free Tier) or Mock | **₹0** |
| **State Storage** | Hosted PostgreSQL / Redis / DynamoDB (\$15–\$40/mo) | Native Git orphan branch (`repopilot-state`) + atomic local cache | **₹0** |
| **Artifact Storage** | S3 / GCS cloud storage bucket (\$5/mo) | GitHub Actions short-retention artifacts (7 days, bounded JSON) | **₹0** |
| **API Gateways** | Paid GitHub Enterprise APIs / SaaS webhooks | Standard GitHub REST API with built-in `GITHUB_TOKEN` | **₹0** |

---

## 2. Enabling the Workflows

To activate RepoPilot in your repository:

### Step 1: Ensure Workflows Are Present
Confirm the four production workflows exist in `.github/workflows/`:
1. `scheduled.yaml`: Runs automated autonomous maintenance on a recurring schedule.
2. `manual-recovery.yaml`: Manual trigger for targeted single-repo recovery or dry runs.
3. `test.yaml`: CI test suite running on pushes and pull requests.
4. `validate-config.yaml`: Preflight validation of repository configurations and security policies.

### Step 2: Enable GitHub Actions in Repository Settings
1. Navigate to **Settings > Actions > General** in your GitHub repository.
2. Under **Actions permissions**, select:
   - **Allow all actions and reusable workflows** (or select **Allow specified actions** and include `actions/*`).
3. Under **Workflow permissions**, select:
   - **Read and write permissions** (required for creating feature branches, submitting pull requests, and pushing persistent run state to `repopilot-state`).
   - Check **Allow GitHub Actions to create and approve pull requests**.
4. Click **Save**.

---

## 3. Setting & Customizing the Schedule

### Default Schedule
Scheduled execution is configured in `.github/workflows/scheduled.yaml`:

```yaml
on:
  schedule:
    # Runs twice daily at 00:00 and 12:00 UTC
    - cron: '0 0,12 * * *'
```

### Schedule Tuning
You can modify the cron schedule to match your workflow:

| Frequency | Cron Expression | Monthly Runs | Monthly Minutes (at ~5 min/run) | Free Quota Impact (Private Repos) |
| :--- | :--- | :---: | :---: | :--- |
| **Twice daily (Default)** | `'0 0,12 * * *'` | ~60 | ~300 min | 15% of 2,000 min limit |
| **Once daily** | `'0 2 * * *'` | ~30 | ~150 min | 7.5% of 2,000 min limit |
| **Every 6 hours** | `'0 */6 * * *'` | ~120 | ~600 min | 30% of 2,000 min limit |
| **Weekly** | `'0 0 * * 0'` | ~4 | ~20 min | 1% of 2,000 min limit |

> [!IMPORTANT]
> **GitHub Actions Scheduling Timing**:
> GitHub Actions schedules run on standard POSIX cron in **UTC**. Scheduled workflows can experience queuing delays during periods of high platform load (typically at the top of the hour). RepoPilot does not rely on sub-second exact execution times and is fully idempotent.

---

## 4. Required Repository Settings & Permissions

### Minimum Permissions Matrix

RepoPilot adheres to the principle of least privilege. Workflows define an empty top-level permission set (`permissions: {}`) and grant specific permissions only to individual jobs:

| Workflow | Job | Granted Permissions | Justification |
| :--- | :--- | :--- | :--- |
| `scheduled.yaml` | `validate-config` | `contents: read` | Inspect configuration files prior to execution |
| `scheduled.yaml` | `run-repopilot` | `contents: write`<br>`pull-requests: write`<br>`issues: read` | Write state branch & feature branches;<br>Open automated PRs;<br>Read open repository issues |
| `manual-recovery.yaml` | `validate-input-and-config` | `contents: read` | Validate sanitized inputs & configuration |
| `manual-recovery.yaml` | `manual-run` | `contents: write`<br>`pull-requests: write`<br>`issues: read` | Execute recovery tasks, open PRs, and update state |
| `test.yaml` | `test` | `contents: read` | Read repository files to run tests (isolated) |
| `validate-config.yaml` | `validate` | `contents: read` | Validate schema and YAML without write credentials |

> [!CAUTION]
> **Never Grant Administration or Unrestricted Permissions**:
> RepoPilot never requests `administration: write` or `security-events: write`. Untrusted code running in sandboxes never receives write tokens.

---

## 5. Required Secrets & Configuration

Navigate to **Settings > Secrets and variables > Actions**:

### 1. `GITHUB_TOKEN` (Built-in)
- **Automatic**: For single-repository operations or repositories within the same account, GitHub automatically generates `secrets.GITHUB_TOKEN`.
- **Cross-Repository Management**: If RepoPilot manages repositories across separate organizations, configure a fine-grained Personal Access Token (PAT) named `GH_TOKEN` with:
  - Repository permissions: `Contents: Read and write`, `Pull requests: Read and write`, `Issues: Read-only`.

### 2. `GEMINI_API_KEY` (Free AI Inference)
- **Provider**: Google AI Studio.
- **Cost**: Free of charge (\$0.00 / ₹0.00).
- **Setup**:
  1. Generate an API key from [Google AI Studio](https://aistudio.google.com).
  2. Save as repository secret `GEMINI_API_KEY`.
- **Degradation**: If omitted or unconfigured, RepoPilot gracefully skips AI-dependent planning without crashing the runner, allowing eligible rule-based and non-AI maintenance to proceed.

---

## 6. How to Trigger a Manual Run

When deploying new configurations, testing target repositories, or recovering from previous failures, execute a controlled manual run:

### Via GitHub Web UI
1. Navigate to the **Actions** tab.
2. Under **Workflows**, select **Manual Recovery Run**.
3. Click **Run workflow** dropdown:
   - **Specific repository to target**: Enter `owner/repo` (e.g. `octocat/my-project`) or leave blank to process all configured repositories.
   - **Run in safe dry-run mode**: Check `true` to plan tasks without creating remote branches or pull requests. Uncheck for live publishing.
   - **Log verbosity level**: Select `DEBUG` or `INFO`.
   - **Path to configuration file**: `config/repos.yaml` (default).
4. Click **Run workflow**.

### Via GitHub CLI (`gh`)
```bash
# Dry-run for all repositories
gh workflow run manual-recovery.yaml -f dry_run=true -f log_level=INFO

# Live targeted run for a single repository
gh workflow run manual-recovery.yaml \
  -f target_repo="octocat/my-project" \
  -f dry_run=false \
  -f log_level=DEBUG
```

---

## 7. Concurrency Controls & Race Condition Prevention

RepoPilot enforces strict workflow serialization through GitHub concurrency groups:

```yaml
concurrency:
  group: repopilot-execution
  cancel-in-progress: false
```

### Behavior Under Load
1. **Mutex Lock**: Both `scheduled.yaml` and `manual-recovery.yaml` share the `repopilot-execution` group. Only one runner can execute at any time.
2. **`cancel-in-progress: false`**: If a scheduled run triggers while a manual run is active, the scheduled run is **queued**, not cancelled. This guarantees that:
   - In-progress Git commits to `repopilot-state` are never aborted midway.
   - Partial pull request publishing operations are completed cleanly.
   - Workspaces and temporary files are fully cleaned up before another run begins.
3. **CI Workflows**: Pull request test workflows (`test.yaml` and `validate-config.yaml`) use `cancel-in-progress: true` to immediately terminate superseded test runs, preserving free runner minutes.

---

## 8. Execution Limits & Budget Enforcement

RepoPilot implements defense-in-depth budgeting at both the workflow and application layers:

```
┌────────────────────────────────────────────────────────┐
│ GitHub Actions Job Timeout: 15 Minutes                 │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Application Budget Tracker: 10 Minutes Max       │  │
│  │  - Max Repositories per Run: 5                   │  │
│  │  - Max Tasks per Repository: 2                   │  │
│  │  - Max Retries per Task: 2                       │  │
│  │  - Max AI Requests per Run: 20                   │  │
│  │  - Per-Task Validation Timeout: 180s             │  │
│  └──────────────────────────────────────────────────┘  │
│  Graceful Stop: Persist State ➔ Step Summary ➔ Exit    │
└────────────────────────────────────────────────────────┘
```

### Graceful Termination Sequence
When execution time approaches the 10-minute application budget:
1. `BudgetTracker.is_time_exhausted()` evaluates to `True`.
2. The orchestrator halts intake of new tasks and completes the current repository safely.
3. Completed task records are written to persistent state.
4. A partial execution report is emitted with full diagnostic details.
5. Step summary is written to `$GITHUB_STEP_SUMMARY`.
6. Process exits cleanly with status code `2` (completed with warnings), preventing runner timeout aborts.

---

## 9. Persistent State & Durable Memory

### The `repopilot-state` Git Branch
Runner instances are completely ephemeral. To achieve durable task memory without paying for a cloud database, RepoPilot stores execution history on an isolated orphan Git branch named `repopilot-state`:

```bash
# View current persistent state
git fetch origin repopilot-state
git show origin/repopilot-state:state.json
```

### State Deduplication Engine
Before proposing tasks, RepoPilot queries `StateStoreInterface.is_duplicate_task()`:
- Tasks matching completed titles or overlapping file sets within the repository history are marked `SKIPPED`.
- Reruns will not duplicate branches or open duplicate pull requests.

### Manually Inspecting or Resetting State
If you wish to re-evaluate all repositories from a clean slate:
```bash
git checkout --orphan repopilot-state
git rm -rf .
cat << 'EOF' > state.json
{
  "version": 1,
  "last_updated": "2026-10-10T00:00:00Z",
  "repositories": {}
}
EOF
git add state.json
git commit -m "chore(ops): reset persistent state"
git push origin repopilot-state --force
```

---

## 10. Inspecting Logs, Step Summaries & Artifacts

### 1. GitHub Actions Step Summary
Every run generates a formatted Markdown Step Summary directly in the GitHub Actions run overview:
- Run ID and execution timestamp.
- Total operating cost indicator (**₹0.00**).
- Repository metrics table (Completed, Failed, Skipped).
- Direct markdown links to opened Pull Requests.
- Error diagnostics for failed validations.

### 2. JSON Diagnostic Report Artifact
Each run produces a structured `repopilot-report.json` or `recovery-report.json`:
- Uploaded via `actions/upload-artifact@4cec3d8aa04e39d1a68397de0c4cd6fb9dce8ec1`.
- **Retention**: Strictly configured to **7 days** to conserve GitHub free storage.
- Contains machine-readable timing, tokens, diffs, and validation check exit codes.

### 3. Exit Codes
- `0`: Completed successfully with zero task-level errors.
- `1`: Configuration schema error or invalid CLI arguments (preflight failure).
- `2`: Completed with task-level validation failures or partial budget timeouts (non-fatal; report captured).
- `3`: Unhandled operational exception.

---

## 11. Free Usage Limitations & Accounting

### GitHub Actions Free Quota Calculation
- **Private Repositories**: 2,000 free minutes per month.
- **Public Repositories**: Unlimited free workflow minutes.
- **Runner Multiplier**:
  - `ubuntu-latest`: **1x multiplier** (1 minute elapsed = 1 minute billed).
  - Windows: 2x multiplier (avoided).
  - macOS: 10x multiplier (avoided).
- **Consumption Analysis**:
  - Scheduled runs: 2 runs/day × 5 minutes average = **10 minutes/day** = **~300 minutes/month**.
  - Headroom: Uses only **15%** of the 2,000 monthly allowance for private repos.

### GitHub Artifact Storage Quota
- Free allowance: 500 MB per account.
- RepoPilot report size: ~5 KB per execution.
- 60 runs × 5 KB = 300 KB / month.
- With 7-day retention, storage usage never exceeds 100 KB (<0.02% of quota).

### AI Provider Quotas (Google AI Studio Free Tier)
- **Model**: `gemini-1.5-flash` or `gemini-2.0-flash`.
- **Rate Limit**: 15 Requests Per Minute (RPM), 1,500 Requests Per Day (RPD).
- **Token Allowance**: 1,000,000 Tokens Per Minute (TPM).
- **RepoPilot Enforcement**: Capped at `max_ai_requests_per_run: 20`. Two runs/day = 40 requests/day (<3% of free daily allowance).

---

## 12. Security Model & Threat Boundaries

### Pinned Action SHAs
All third-party GitHub Actions are pinned to full 40-character commit SHAs with semantic version comments to eliminate supply-chain injection attacks:
- `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683` (`v4.2.2`)
- `actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38` (`v5.4.0`)
- `actions/upload-artifact@4cec3d8aa04e39d1a68397de0c4cd6fb9dce8ec1` (`v4.6.1`)

### Input Sanitization & Script Injection Defense
In `manual-recovery.yaml`, dispatch inputs are mapped directly to environment variables (`INPUT_TARGET_REPO`, `INPUT_CONFIG_PATH`) rather than expanded directly in shell strings. Input values are validated against strict regex allowlists before shell execution.

### Environment Secret Scrubbing
`ValidationRunner` sanitizes the execution environment before running repository test commands:
- Strips `GITHUB_TOKEN`, `GH_TOKEN`, `GEMINI_API_KEY`, and cloud provider tokens.
- Untrusted repository code cannot exfiltrate deployment credentials.

### Mandatory Excluded Paths
Changes touching prohibited system paths are rejected immediately:
- `.github/workflows/`
- `secrets/`
- `.env`
- Private key files and certificates.

---

## 13. Common Failures & Solutions

### Issue: "Action failed with: Resource not accessible by integration"
- **Cause**: Workflow lacks permission to create pull requests or push commits.
- **Fix**: Go to **Settings > Actions > General > Workflow permissions** and select **Read and write permissions**, and check **Allow GitHub Actions to create and approve pull requests**.

### Issue: "Configuration validation failed: version: invalid"
- **Cause**: `config/repos.yaml` specifies an unsupported schema version or invalid syntax.
- **Fix**: Run `repopilot --config config/repos.yaml` locally or run the `validate-config.yaml` workflow to pinpoint the exact schema violation.

### Issue: "HTTP 429: Resource has been exhausted (Gemini)"
- **Cause**: Free-tier rate limits reached on Google AI Studio.
- **Fix**: RepoPilot handles this automatically: it logs a warning, skips remaining AI tasks for the current cycle, and continues non-AI work. Quotas reset every minute.

### Issue: "Validation command timed out after 180 seconds"
- **Cause**: Target repository test suite took longer than `timeout_seconds`.
- **Fix**: In `config/repos.yaml`, increase `validation.timeout_seconds` (up to 300) or specify a faster test command (e.g. `pytest tests/unit/ -m 'not slow'`).

### Issue: "Push failed for branch repopilot-state"
- **Cause**: Concurrent write to the state branch.
- **Fix**: The workflow includes automated fetch-and-rebase retry logic. If a conflict occurs, state will safely reconcile on the subsequent run.

---

*RepoPilot Operations Guide — Maintained autonomously with zero external subscriptions.*
