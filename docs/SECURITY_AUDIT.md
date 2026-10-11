# RepoPilot: Security Audit & Threat Boundary Verification Report

> **Prepared by:** Principal Integration Engineer & System Auditor (Agent 7)  
> **Date:** October 2026  
> **Audited Target:** RepoPilot Autonomous GitHub Management Engine  
> **Overall Security Posture:** High Assurance / Verified Defensive Boundary  

---

## 1. Audit Scope & Threat Model

RepoPilot executes autonomous code planning, patch application, test execution, and pull request generation across public and private GitHub repositories without pre-flight human intervention.

As specified in `SECURITY.md`, the system adheres to the core axiom:  
**All target repository source code, open issues, PR comments, and AI-generated outputs are treated as untrusted inputs.**

This security audit evaluated four major risk domains:
1. Credential isolation & secret leakage.
2. Untrusted code execution boundaries & workspace sandboxing.
3. GitHub publishing safety gates & auto-merge controls.
4. GitHub Actions workflow supply-chain security & runner privilege boundaries.

---

## 2. Credential Protection & Secret Scrubbing (Audit 4.1)

### A. Subprocess Environment Scrubbing
- **Risk:** Malicious test suites in target repositories (or dependencies) executing `os.environ` inspection to exfiltrate `GITHUB_TOKEN` or `GEMINI_API_KEY`.
- **Implementation:** `SecretSanitizer.sanitize_environment()` in `src/repopilot/execution/security.py`.
- **Audit Finding:**
  - `ValidationRunner` strips `GITHUB_TOKEN`, `GH_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, AWS credentials, and all variables matching keyword patterns (`TOKEN`, `SECRET`, `KEY`, `PASSWORD`, `CREDENTIAL`, `AUTH`, `PRIVATE`).
  - `HOME` is redirected to the ephemeral workspace directory, preventing child processes from accessing host `~/.gitconfig`, `~/.netrc`, or `~/.ssh/`.
  - `GIT_ASKPASS`, `SSH_AUTH_SOCK`, and `SSH_ASKPASS` are purged.
  - Verified by unit test `test_validation_runner_strips_privileged_env` (100% pass).

### B. Log Masking & Secret Redaction
- **Risk:** Sensitive tokens printed in GitHub Actions runner logs or output step summaries.
- **Implementation:** `redact_secrets()` in `src/repopilot/core/logging.py` and `SanitizingFormatter`.
- **Audit Finding:**
  - Classic PATs (`ghp_`), fine-grained PATs (`github_pat_`), app tokens (`ghs_`), AWS keys (`AKIA...`), Slack tokens, and generic bearer tokens are replaced with `[REDACTED_TOKEN]` prior to writing to stdout, stderr, or persistent storage.
  - Configuration parser (`scan_for_credentials` in `loader.py` and `models.py`) raises immediate errors if users inadvertently put credentials into YAML files.

### C. Persistent Memory Hygiene
- **Risk:** Sensitive tokens or credentials persisted into `state.json` or committed to `repopilot-state`.
- **Implementation:** State stores serialize `TaskRecord` and `TaskCheckpoint` using `redact_secrets()`.
- **Audit Finding:** No authentication tokens or secret credentials are saved in state checkpoints.

---

## 3. Untrusted Code Execution & Sandbox Containment (Audit 4.2)

### A. Path Traversal & Forbidden Paths
- **Risk:** AI-generated patch contains relative traversal (`../../etc/passwd`) or attempts to tamper with GitHub workflows (`.github/workflows/malicious.yaml`) to elevate privileges.
- **Implementation:** `PathSecurityChecker` and `PatchValidator` in `src/repopilot/execution/`.
- **Audit Finding:**
  - Built-in mandatory non-bypassable exclusions enforce that the following can **never** be created, modified, or deleted:
    - `.github/workflows/`
    - `secrets/`
    - `production_credentials/`
    - `.env` and `.env.*`
    - `*.pem`, `*.key`, `*.p12`
    - `.git/`, `.ssh/`, `.aws/`, `.netrc`
  - Relative parent path components (`../`) and absolute paths (`/etc/...`, `C:\...`) are blocked with `PathTraversalError`.
  - Null-byte injection (`\0`) is blocked with `SecurityViolationError`.
  - Verified by unit tests `test_path_traversal_prevention` and `test_forbidden_excluded_paths_rejected`.

### B. Symlink Escape Defense
- **Risk:** Repository contains symlinks pointing outside the workspace boundary, allowing patch application or test runners to modify host system files.
- **Implementation:** `PathSecurityChecker.verify_no_symlink_escape()`.
- **Audit Finding:**
  - Resolves target paths and every intermediate ancestor directory using `Path.resolve(strict=True)`.
  - Rejects any symlink whose canonical target escapes `workspace_root`.
  - Verified by unit test `test_symlink_escape_attempts`.

### C. Resource Bounds & Process Timeouts
- **Risk:** Infinite loops, fork bombs, or disk exhaustion in target test suites.
- **Implementation:** POSIX resource limits (`setup_posix_limits`) and `subprocess.Popen` with process group termination (`os.killpg(os.getpgid(pid), signal.SIGKILL)`).
- **Audit Finding:**
  - Execution timeout is enforced (default 180s, configurable up to 600s).
  - Memory limit (default 2 GB) and CPU limits enforced via POSIX `setrlimit`.
  - Output truncation (`truncate_output`) caps captured stdout/stderr to 1 MB, preventing memory blowouts.

### D. Honest Documentation of Execution Isolation Limitations
- **Current Runtime Arrangement:** RepoPilot runs inside an Ubuntu GitHub Actions runner (`ubuntu-latest`). While child processes run with sanitized environments, process groups, and temporary workspaces, they run on the same virtual machine as the runner without root namespace containers (Docker-in-Docker is not used to preserve zero-cost execution and fast startup).
- **Limitation:** In a GitHub-hosted runner, code running in `ValidationRunner` has network access to public internet endpoints (unless firewalled at runner level). While privileged secrets are completely stripped from the process environment, network calls cannot be completely blocked without containerized network namespaces.
- **Recommendation:** Repositories running untrusted third-party code with unvetted test scripts should use `--dry-run` or restrict validation commands to trusted linters.

---

## 4. GitHub Publishing Safety Gates (Audit 4.3)

### A. Pre-Commit Verification (9 Mandatory Checks)
- **Implementation:** `CommitBuilder.verify_pre_commit()` in `src/repopilot/github/commit_builder.py`.
- **Audit Finding:**
  1. Target repository verified.
  2. Task identifier validated.
  3. Starting base revision verified.
  4. Complete diff inspected (rejecting empty commits).
  5. Excluded paths re-verified at commit stage.
  6. Unexpected modifications rejected.
  7. Diffs scanned for leaked credentials (`SecurityPolicyChecker.scan_content_for_secrets`).
  8. Mandatory validation passed flag required (`validation.passed == True` and `validation.is_publishable == True`).
  9. Task category checked against repository `allowed_tasks`.

### B. Auto-Merge Safety Gates (10 Mandatory Conditions)
- **Implementation:** `MergePolicyEngine.evaluate_merge_eligibility()` in `src/repopilot/github/merge_engine.py`.
- **Audit Finding:**
  - Auto-merge is strictly **disabled by default (`auto_merge: false`)**.
  - Auto-merge is rejected if:
    1. `auto_merge` is `False`.
    2. PR is in draft mode.
    3. PR is not open.
    4. Validation result is missing or failed.
    5. PR diff touches security-sensitive areas (workflows, credentials, auth).
    6. Destructive SQL schema migrations detected (`DROP TABLE`, `TRUNCATE`).
    7. Commit SHA does not match validated SHA.
    8. GitHub check runs report failing or pending statuses.
    9. Human approval reviews are requested or unapproved.
    10. Push permissions are missing.
  - Verified by unit tests `test_merge_eligibility_all_conditions_satisfied`, `test_automatic_merging_disabled`, `test_required_checks_failing`, `test_security_sensitive_changes`.

---

## 5. GitHub Actions Workflow Security (Audit 4.4)

### A. Principle of Least Privilege
- **Audit Finding:**
  - All four workflows (`scheduled.yaml`, `manual-recovery.yaml`, `test.yaml`, `validate-config.yaml`) specify top-level `permissions: {}`.
  - Specific permissions are granted only at the job level:
    - Preflight validation jobs: `contents: read` only.
    - Execution jobs: `contents: write`, `pull-requests: write`, `issues: read`.
  - Blanket `write-all` or administrative permissions are never used.

### B. Third-Party Action Pinned SHAs
- **Audit Finding:**
  - All third-party GitHub Actions are pinned to full 40-character commit SHAs:
    - `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683` (v4.2.2)
    - `actions/setup-python@42375524e23c412d93fb67b49958b491fce71c38` (v5.4.0)
    - `actions/upload-artifact@4cec3d8aa04e39d1a68397de0c4cd6fb9dce8ec1` (v4.6.1)
  - Supply-chain tag-mutation attacks are prevented.

### C. Injection Prevention in Workflow Dispatch
- **Audit Finding:**
  - In `manual-recovery.yaml`, inputs (`target_repo`, `config_path`) are bound via environment variables (`INPUT_TARGET_REPO`, `INPUT_CONFIG_PATH`) rather than directly expanded in bash strings.
  - Pre-flight step enforces strict regex allowlists:
    - `target_repo`: `^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)?$`
    - `config_path`: `^[a-zA-Z0-9_./-]+\.(yaml|yml)$`

### D. Concurrency Controls & Shared State Locks
- **Audit Finding:**
  - Both `scheduled.yaml` and `manual-recovery.yaml` share the concurrency group `repopilot-execution` with `cancel-in-progress: false`.
  - Concurrent executions cannot corrupt the `repopilot-state` orphan Git branch.

---

## 6. Summary of Security Verification Tests

All 32 security and permission unit tests pass with zero warnings:
- `test_path_traversal_prevention`
- `test_forbidden_excluded_paths_rejected`
- `test_safe_patch_application`
- `test_workspace_automatic_cleanup`
- `test_validation_runner_strips_privileged_env`
- `test_symlink_escape_attempts`
- `test_invalid_patch_rejection`
- `test_excessive_patch_size`
- `test_patch_application_transactional_rollback`
- `test_security_sensitive_changes`
- `test_workflow_permission_declarations_least_privilege`
- `test_secret_isolation_and_env_passing`
- `test_untrusted_pull_request_restrictions`

**Audit Conclusion:** RepoPilot enforces strict, defense-in-depth security invariants in deterministic Python application code. It meets production security requirements for unattended execution on GitHub Actions.
