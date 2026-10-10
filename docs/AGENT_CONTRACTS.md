# RepoPilot: Parallel Agent Contracts

This document specifies the strict technical contracts, module ownership boundaries, interface definitions, and acceptance criteria for the six parallel engineering agents implementing RepoPilot.

---

## Global Agent Guidelines

1. **Strict Zero-Cost Rule**: No agent may introduce dependencies on paid services, cloud subscriptions, or unmetered external APIs.
2. **Immutable Interfaces**: Core types in `src/repopilot/core/` are frozen. If a change is needed, it must be backward-compatible.
3. **No File Overlaps**: Agents must strictly modify only their designated files. Modifying files owned by another agent will cause merge conflicts.
4. **Mandatory Tests**: Every agent is responsible for unit and integration tests covering 100% of their new methods.
5. **Security First**: Code changes must never weaken the security boundary (no path traversal, no secret leakage, no unvalidated merges).

---

## Agent 1 — Repository Intelligence

### 1. Responsibilities
- Deep repository AST analysis and dependency file parsing.
- Precise framework and language detection (Python, TypeScript/JS, Rust, Go, Java, C/C++).
- Test runner and linter command autodetection (e.g. detect `pytest` in `pyproject.toml`, `npm test` in `package.json`).
- Extraction of high-priority context from open GitHub issues, recent commits, and PR feedback.
- Documentation discovery and quality scoring (identifying missing README sections, missing type stubs, or stale docs).

### 2. Files & Directories Owned
- `src/repopilot/intelligence/` (all files)
- `tests/unit/test_intelligence.py`

### 3. Files Forbidden to Modify
- `src/repopilot/core/*`
- `src/repopilot/execution/*`
- `src/repopilot/github/*`
- `src/repopilot/orchestration/*`
- `src/repopilot/config/*`

### 4. Existing Interfaces to Implement & Extend
- `RepositoryAnalyzerInterface` in `src/repopilot/intelligence/analyzer.py`
```python
class RepositoryAnalyzerInterface(ABC):
    @abstractmethod
    def analyze(self, workspace_path: Path, issues: Optional[List[GitHubIssue]] = None) -> RepoContext:
        pass
```

### 5. Required Tests
- Test detection of Python projects with poetry, uv, flit, setup.py, pyproject.toml.
- Test detection of Node/TypeScript projects with npm, yarn, pnpm, bun.
- Test parsing of test framework commands when no explicit config is provided.
- Test handling of empty repositories or non-code documentation repositories.
- Test bounded extraction of issue details without exceeding memory limits.

### 6. Acceptance Criteria
- Produces a complete `RepoContext` containing accurately detected languages, frameworks, documentation files, and open issue summaries.
- Execution completes within 2.5 seconds on repositories containing up to 10,000 files.
- Zero crashes on unreadable or binary files.

### 7. Dependencies on Other Modules
- Consumes `GitHubIssue` from `repopilot.github.models`.
- Returns `RepoContext` defined in `repopilot.intelligence.analyzer`.

---

## Agent 2 — AI Planning and Development

### 1. Responsibilities
- Free-tier inference provider integrations (Google Gemini 1.5/2.0 Flash, HuggingFace Inference API, Ollama/local if present).
- Structured prompt engineering and JSON response validation for code planning and generation.
- Bounded repair planner: analyze test failure output (stdout/stderr) and generate corrective patches.
- Context window management: token estimation, truncation of large files, AST chunking.
- Graceful degradation: detect rate limits (HTTP 429) or missing keys and return empty candidate tasks without crashing.

### 2. Files & Directories Owned
- `src/repopilot/ai/` (all files)
- `tests/unit/test_ai_provider.py`
- `tests/unit/test_repair_planner.py`

### 3. Files Forbidden to Modify
- `src/repopilot/execution/*`
- `src/repopilot/github/*`
- `src/repopilot/orchestration/*`
- `src/repopilot/state/*`

### 4. Existing Interfaces to Implement & Extend
- `AIProviderInterface` in `src/repopilot/ai/provider.py`
```python
class AIProviderInterface(ABC):
    @abstractmethod
    def get_name(self) -> str: pass
    @abstractmethod
    def get_status(self) -> ProviderStatus: pass
    @abstractmethod
    def plan_improvements(self, request: AIPlanRequest) -> List[Task]: pass
    @abstractmethod
    def generate_code_change(self, request: AICodeChangeRequest) -> Optional[ChangePlan]: pass
```

### 5. Required Tests
- Test handling of rate-limit responses (HTTP 429) and exponential backoff.
- Test response validation when AI returns malformed JSON or markdown code blocks.
- Test retry logic with simulated previous test failure stderr.
- Test zero-cost invariant: ensure `estimated_cost_usd` is 0.0 and never calls paid endpoints.

### 6. Acceptance Criteria
- Returns valid, actionable `ChangePlan` objects matching the target repository's technology stack.
- Safely handles API timeout, quota exhaustion, or offline network state without raising unhandled exceptions.
- Corrective patch generation addresses the specific errors reported in `validation_errors`.

### 7. Dependencies on Other Modules
- Consumes `Task`, `ChangePlan`, `FileChange`, `ValidationResult` from `repopilot.core.models`.

---

## Agent 3 — Execution and Validation

### 1. Responsibilities
- Isolated workspace management: ephemeral sandbox creation, copy/clone, and guaranteed cleanup.
- Defensive patch application: strict validation against path traversal (`../`), null-byte injection, and symlink attacks.
- Prohibited paths enforcement: block any change to `.github/workflows/`, `secrets/`, `.env`, certificates, or private keys.
- Sandbox test execution: execute repository test and lint commands with timeouts and resource limits.
- Environment sanitization: scrub all credentials (`GITHUB_TOKEN`, API keys) from the execution environment of untrusted repository code.

### 2. Files & Directories Owned
- `src/repopilot/execution/` (all files)
- `src/repopilot/validation/` (all files)
- `tests/unit/test_execution_security.py`
- `tests/unit/test_validation_runner.py`

### 3. Files Forbidden to Modify
- `src/repopilot/ai/*`
- `src/repopilot/github/*`
- `src/repopilot/state/*`
- `src/repopilot/reporting/*`

### 4. Existing Interfaces to Implement & Extend
- `PatchValidator` and `PatchApplier` in `src/repopilot/execution/patch.py`.
- `ValidationRunner` in `src/repopilot/validation/runner.py`.

### 5. Required Tests
- Comprehensive path traversal attacks (`../../etc/shadow`, symlinks outside workspace).
- Excluded paths bypass attempts (e.g. `.github/workflows/ci.yml`, `.env.production`).
- Time-out test execution (ensuring hanging test processes are terminated).
- Verification that `GITHUB_TOKEN` is not present in child process environments.

### 6. Acceptance Criteria
- 100% of patch application attempts outside the workspace or touching excluded files are rejected with `SecurityViolationError`.
- Validation commands terminate within configured timeout seconds.
- Ephemeral workspace directories are cleaned up even upon unhandled exceptions.

### 7. Dependencies on Other Modules
- Consumes `FileChange`, `ChangePlan`, `ValidationResult`, `ValidationCheck` from `repopilot.core.models`.

---

## Agent 4 — GitHub Operations

### 1. Responsibilities
- Real GitHub REST API client operations: branch creation, commit creation, pull request generation, label management.
- Multi-repository discovery from user configuration.
- Merge policy evaluation: enforce that `auto_merge` is only performed when explicitly configured AND CI checks pass.
- Branch and PR conflict handling: detect existing PRs for the same branch or task and handle updates cleanly.
- Full offline simulation mode with `MockGitHubClient`.

### 2. Files & Directories Owned
- `src/repopilot/github/` (all files)
- `tests/unit/test_github_client.py`

### 3. Files Forbidden to Modify
- `src/repopilot/ai/*`
- `src/repopilot/execution/*`
- `src/repopilot/validation/*`
- `src/repopilot/state/*`

### 4. Existing Interfaces to Implement & Extend
- `GitHubClientInterface` in `src/repopilot/github/client.py`.
```python
class GitHubClientInterface(ABC):
    def get_repo(self, owner: str, name: str) -> GitHubRepoInfo: pass
    def list_open_issues(self, owner: str, name: str) -> List[GitHubIssue]: pass
    def create_branch(self, owner: str, name: str, base_branch: str, new_branch: str) -> bool: pass
    def create_pull_request(self, owner: str, name: str, title: str, body: str, head_branch: str, base_branch: str, draft: bool = False) -> GitHubPullRequest: pass
    def merge_pull_request(self, owner: str, name: str, pr_number: int, commit_title: str) -> bool: pass
```

### 5. Required Tests
- Test PR creation with draft and non-draft flags.
- Test branch creation and handling of existing branch names.
- Test rate limit handling: read `X-RateLimit-Remaining` and backoff appropriately.
- Test auto-merge enforcement: verify merge is blocked when `auto_merge=False`.

### 6. Acceptance Criteria
- Fully functional REST client using standard library `urllib` without requiring heavy external dependencies.
- Detailed, descriptive PR bodies containing modified file lists, rationale, and validation status.
- Zero credentials logged or leaked in error responses.

### 7. Dependencies on Other Modules
- Consumes `GitHubRepoInfo`, `GitHubIssue`, `GitHubPullRequest` from `repopilot.github.models`.

---

## Agent 5 — GitHub Actions and Operations

### 1. Responsibilities
- Production GitHub Actions workflow maintenance: `scheduled.yaml`, `manual-recovery.yaml`, `test.yaml`.
- Runner concurrency management (mutex locks via GitHub concurrency groups).
- Runner execution budget management (timeouts, memory bounds, caching).
- Setup instructions, permissions documentation, and operational troubleshooting guides (`docs/OPERATIONS.md`).
- Disaster recovery and workflow repair mechanisms.

### 2. Files & Directories Owned
- `.github/workflows/` (all files)
- `docs/OPERATIONS.md`
- `tests/unit/test_workflows_syntax.py`

### 3. Files Forbidden to Modify
- `src/repopilot/core/*`
- `src/repopilot/ai/*`
- `src/repopilot/execution/*`
- `src/repopilot/github/*`

### 4. Existing Interfaces to Implement & Extend
- GitHub Actions workflow configurations and YAML syntax.

### 5. Required Tests
- YAML syntax validation of all files in `.github/workflows/`.
- Concurrency group check to verify overlapping runs cannot occur.
- Minimal permissions check (ensure no blanket `write-all` permissions).

### 6. Acceptance Criteria
- Workflows use immutable commit SHAs for third-party actions.
- Explicit job timeouts prevent runaway runners from consuming free minutes.
- Step summaries render cleanly in the GitHub Actions UI.

### 7. Dependencies on Other Modules
- Invokes `repopilot` CLI entrypoint in `src/repopilot/main.py`.

---

## Agent 6 — Persistence and Reporting

### 1. Responsibilities
- Configuration file schema expansion and validation (`src/repopilot/config/`).
- Persistent state management across runner teardowns using the dedicated `repopilot-state` Git orphan branch or local storage.
- Duplicate task detection and state deduplication algorithms.
- Checkpointing: record progress after each task and repository to allow recovery if interrupted.
- Execution reporting: structured JSON logs, daily summaries, and GitHub Actions Markdown Step Summary.

### 2. Files & Directories Owned
- `src/repopilot/config/` (all files)
- `src/repopilot/state/` (all files)
- `src/repopilot/reporting/` (all files)
- `tests/unit/test_config.py`
- `tests/unit/test_state.py`
- `tests/unit/test_reporting.py`

### 3. Files Forbidden to Modify
- `src/repopilot/ai/*`
- `src/repopilot/execution/*`
- `src/repopilot/github/*`

### 4. Existing Interfaces to Implement & Extend
- `StateStoreInterface` in `src/repopilot/state/store.py`.
- `ExecutionReporter` in `src/repopilot/reporting/reporter.py`.
- `load_config` in `src/repopilot/config/loader.py`.

### 5. Required Tests
- Atomic state writes and recovery from corrupted state files.
- Duplicate task detection across multiple runs.
- Markdown table formatting in GitHub Step Summary generation.
- Configuration validation rejecting out-of-range parameters.

### 6. Acceptance Criteria
- State files are atomically written and never corrupted during aborts.
- State contains zero tokens, secrets, or sensitive code contents.
- Report summaries accurately reflect success, failure, and cost metrics (₹0.00).

### 7. Dependencies on Other Modules
- Consumes `Task`, `ExecutionReport`, `RepoPilotConfig` from `repopilot.core.models`.
