# Execution Sandbox & Validation Subsystem (Agent 3)

The Execution Sandbox and Validation subsystem safely receives AI-proposed code changes, validates and applies them within isolated workspaces, runs project-aware verification checks, classifies failures into structured categories, and enforces defensive security boundaries before changes can ever be published.

---

## 1. Architectural Overview

Untrusted AI patches and third-party repository code are never executed directly in the privileged orchestration process, and are never granted access to GitHub credentials or sensitive environment variables.

```
AI Proposal (ChangePlan)
         │
         ▼
[PatchValidator] ──► Checks: Syntax, size limits, conflict checks,
         │           Path traversal (../), Symlink escapes, Prohibited files
         ▼
[IsolatedWorkspace] ──► Ephemeral filesystem sandbox, starting revision record,
         │              transactional backup & rollback, disk usage quota
         ▼
[ValidationRunner] ──► Scrubbed environment (no tokens/secrets),
         │             POSIX rlimits (memory, CPU), process group timeout kill,
         │             project test & lint execution (Python, Node/TS, Go, Rust)
         ▼
[FailureClassifier & Policy] ──► 10 normalized failure categories,
                                  recoverability check, publication eligibility
```

---

## 2. Workspace Lifecycle

1. **Creation**:
   - `WorkspaceManager` creates an ephemeral directory prefixed with `task_<task_id>_`.
   - Each task receives its own independent workspace; concurrent tasks never share writable files.
   - Enforces `max_concurrent_tasks`.

2. **Population & Starting Revision Recording**:
   - Files are copied into the workspace from the source repository or revision checkout.
   - Symlinks in the source directory are validated: any symlink pointing outside the source tree is rejected or skipped.
   - The starting revision is recorded (Git `HEAD` commit SHA if available, or SHA-256 manifest of the workspace tree).

3. **Modification & Tracking**:
   - Every file modified, created, or deleted is recorded in `_modified_files`.
   - Before applying patches, a snapshot of affected files is captured to enable immediate rollback on error.

4. **Resource Enforcement**:
   - Disk usage is calculated during operations; exceeding `max_disk_bytes` raises `ResourceLimitExceededError`.

5. **Cleanup**:
   - On exit or exception (including `KeyboardInterrupt`), `cleanup()` removes the workspace directory using `shutil.rmtree` with automatic read-only permission clearing (`_remove_readonly`).

---

## 3. Patch Validation Rules

Before any modification is written to disk, `PatchValidator` and `PatchApplier` verify:

- **Structural Validity**:
  - Path must be non-empty and non-whitespace.
  - Path must not contain null bytes (`\0`).
  - Absolute paths (e.g. `/etc/passwd`, `C:\Windows`) are rejected immediately.
  - Content must be well-formed strings.

- **Path Containment & Traversal Defense**:
  - Relative parent navigation (`../`, `dir/../../escape`) is resolved against `workspace_root`.
  - Target must reside strictly within `workspace_root`.

- **Symlink Escape Defense**:
  - Neither target path nor any existing ancestor directory may be a symlink pointing outside the workspace.
  - If a symlink points outside the workspace root, `SymlinkEscapeError` is raised.

- **Prohibited & Excluded Files**:
  - Non-bypassable mandatory exclusions:
    - `.github/workflows/` (workflow policy files)
    - `secrets/` and `production_credentials/`
    - `.env` and `.env.*`
    - `.git/` internals
    - `.ssh/` and `.aws/` credential files
    - Private keys (`id_rsa`, `id_ed25519`, `id_ecdsa`)
    - Shell profiles (`.bashrc`, `.bash_profile`, `.zshrc`)
  - Any custom repository `excluded_paths` configured in `repos.yaml`.

- **Conflict & Ambiguity Detection**:
  - Multiple file changes targeting the same file within a single `ChangePlan` with conflicting actions or content are rejected (`ConflictingPatchError`).

- **Resource Limits**:
  - Maximum changed files (default: 25).
  - Maximum single file size (default: 1 MB).
  - Maximum total patch size (default: 2 MB).

- **Transactional Rollback**:
  - If any file write fails or a security check fails midway through a patch, all previously touched files are restored to their exact pre-patch state.

---

## 4. Supported Project Ecosystems & Test Discovery

`TestDiscoverer` inspects the workspace to identify project ecosystems and existing validation commands:

1. **Python**:
   - Detects `pyproject.toml`, `setup.cfg`, `pytest.ini`, `tox.ini`.
   - Detects test directories (`tests/`) and test files (`test_*.py`, `*_test.py`).
   - Default test command: `pytest`.
   - Discovers linters: `ruff check .`, `flake8`, `mypy .`.

2. **JavaScript & TypeScript (Node.js)**:
   - Detects `package.json`, `tsconfig.json`.
   - Package manager detection: `pnpm`, `yarn`, `bun`, or `npm`.
   - Extracts scripts: `"test"`, `"lint"`, `"typecheck"`.
   - Discovers linters: `npm run lint`, `npx tsc --noEmit`.

3. **Rust**:
   - Detects `Cargo.toml`.
   - Commands: `cargo test`, `cargo clippy -- -D warnings`.

4. **Go**:
   - Detects `go.mod` and `*_test.go` files.
   - Commands: `go test ./...`, `golangci-lint run`.

### No-Test Repository Policy
If a repository contains no test configurations and no test files:
- It is reported clearly: `has_tests = False`.
- When `require_validation` is enabled (default `True`), the change is **not** considered validated, and `is_publishable` is set to `False`.

---

## 5. Execution Limits & Resource Bounds

| Resource | Default Limit | Enforcement Mechanism |
| :--- | :--- | :--- |
| **Command Timeout** | 180 seconds | `subprocess.Popen` timeout with `os.killpg` process group termination |
| **Output Capture** | 10 KB (10,240 bytes) | `truncate_output` keeps head/tail with notice `[Output truncated]` |
| **Memory Limit** | 1,024 MB (1 GB) | POSIX `resource.setrlimit(RLIMIT_AS)` where supported |
| **CPU Time Limit** | 180 seconds | POSIX `resource.setrlimit(RLIMIT_CPU)` where supported |
| **Workspace Disk** | 500 MB | Recursive directory size inspection in `IsolatedWorkspace` |
| **Patch Size** | 2 MB | Byte count validation before patch application |
| **Files in Patch** | 25 files | File count check in `PatchValidator` |

---

## 6. Failure Classification

Validation failures are categorized into 10 structured types:

1. `PATCH_APPLICATION_FAILURE`: Diff application failed, syntax error in patch, or conflicting modifications.
2. `MISSING_DEPENDENCIES`: Uninstalled Python/Node packages (`ModuleNotFoundError`, `Cannot find module`).
3. `TEST_FAILURE`: Assertion errors, test suite failures.
4. `LINT_FAILURE`: Code style, type errors, or formatting violations.
5. `SYNTAX_ERROR`: Syntax errors, indentation errors, compilation parse errors.
6. `EXECUTION_TIMEOUT`: Process killed due to exceeding timeout seconds.
7. `RESOURCE_LIMIT_EXCEEDED`: Out-of-memory (OOM, exit 137), disk quota exceeded.
8. `PROHIBITED_FILE_CHANGE`: Security policy violation (modifying `.github/workflows/`, path traversal, symlink escape).
9. `ENVIRONMENT_SETUP_FAILURE`: Command not found (exit 127), missing runtime.
10. `UNKNOWN_FAILURE`: Unhandled non-zero exit codes.

### Recoverability Distinction
- **Recoverable via AI Code Patch**:
  - `TEST_FAILURE`, `LINT_FAILURE`, `SYNTAX_ERROR`, `PATCH_APPLICATION_FAILURE`.
- **Non-Recoverable via AI Code Patch** (Must stop immediately to avoid wasting inference budget):
  - `ENVIRONMENT_SETUP_FAILURE`, `MISSING_DEPENDENCIES` (environment-level), `PROHIBITED_FILE_CHANGE` (security block), `RESOURCE_LIMIT_EXCEEDED`, `EXECUTION_TIMEOUT`.

---

## 7. Bounded Repair Loop

`RepairLoopController` governs retries:
1. When validation fails, a structured `FailureReport` is generated.
2. If `failure_report.is_recoverable` is `False`, the repair loop terminates immediately.
3. If recoverable, repair attempts are allowed up to `min(settings.max_retries_per_task, task.max_retries)` (default: 2).
4. When attempts reach the budget, execution halts with a clear `budget exhausted` diagnostic.
5. Infinite repair loops are strictly prevented.

---

## 8. Publishing Eligibility

`PublishingEligibility.evaluate(result)` verifies that:
1. `result.passed` is `True`.
2. All individual checks passed (no timeouts or errors).
3. If validation is mandatory, the repository must have valid automated tests (`has_tests = True`).
4. No security violations or prohibited paths were touched.

If any criterion fails, `is_publishable` is `False`, preventing Agent 4 from publishing branches or pull requests.

---

## 9. Security Boundaries & Credential Separation

- **Subprocess Environment Sanitization**:
  - All privileged tokens and secrets (`GITHUB_TOKEN`, `GH_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, `AWS_*`, etc.) are stripped from `os.environ` before spawning subprocesses.
  - Any environment variable matching `*TOKEN*`, `*KEY*`, `*SECRET*`, `*PASSWORD*`, `*CREDENTIAL*`, or `*AUTH*` is removed.
- **Isolated Home**:
  - `HOME` is redirected to the workspace directory to prevent untrusted code from reading `~/.ssh`, `~/.aws`, `~/.netrc`, or `~/.gitconfig`.
  - Git credential helpers (`GIT_ASKPASS`, `SSH_AUTH_SOCK`) are cleared.
- **Zero Privileges for Validation**:
  - Validation runs with read-only permissions relative to GitHub. Publishing operations belong strictly to Agent 4 and run after validation completes.

---

## 10. Known Isolation Limitations

1. **Subprocess Isolation vs. Hardware Virtualization**:
   - Workspaces run as child processes under the runner's user account.
   - On GitHub-hosted free runners without root or container privileges, chroot/jail containers are not enabled by default.
   - Operating system limits (`RLIMIT_AS`, `RLIMIT_CPU`) provide process-level bounds, but do not provide full kernel virtualization.
2. **Platform Variability**:
   - On macOS, `RLIMIT_AS` may be restricted or have no effect on child processes depending on OS SIP policies; timeouts and disk checks remain fully active.
3. **Network Access**:
   - Unless network namespaces are configured at the host runner level, child processes retain localhost network access. Defensive sanitization ensures credentials cannot be transmitted.
