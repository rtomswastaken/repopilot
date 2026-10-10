# RepoPilot: Master Implementation Plan

This document details the multi-phase roadmap for developing, testing, integrating, and operating RepoPilot as an autonomous, zero-cost developer.

---

## 1. Foundation Phase (Completed by Main Builder)

- [x] **Repository Architecture Setup**: Modular directory structure (`core`, `config`, `intelligence`, `ai`, `execution`, `validation`, `github`, `state`, `reporting`, `orchestration`).
- [x] **Strict Configuration Validation**: Pydantic models with safety constraints, mandatory excluded paths, and default disabled auto-merge.
- [x] **Core Domain Models & Exceptions**: Typed domain entities (`Task`, `ChangePlan`, `ValidationResult`, `ExecutionReport`).
- [x] **Security Boundaries**: Isolated workspace management, path traversal defense, prohibited path enforcement, secret scrubbing in logs and child processes.
- [x] **Zero-Cost Inference Interface**: Pluggable AI provider abstraction with `MockAIProvider` and free-tier `GeminiProvider`.
- [x] **GitHub Integration**: Typed GitHub client interface with full `MockGitHubClient` and lightweight standard-library `RestGitHubClient`.
- [x] **Persistent State Engine**: Atomic file storage and Git branch state store interface with duplicate task avoidance.
- [x] **Orchestration Lifecycle**: 17-step autonomous workflow with multi-repository fault isolation and budget enforcement.
- [x] **CI/CD & Workflows**: GitHub Actions workflows for tests, scheduled runs, and manual recovery.
- [x] **Testing & Verification**: 24 unit and integration tests passing at 100%.

---

## 2. Parallel Engineering Phase (Six Independent Work Packages)

The core interfaces are locked and fully verified. Six parallel engineering agents can now extend specific domains without merge conflicts.

```
                  ┌──────────────────────────────────────────────┐
                  │           MAIN BUILDER FOUNDATION            │
                  │   Core Models, Orchestrator, CLI, Interfaces │
                  └──────────────────────┬───────────────────────┘
                                         │
        ┌──────────────┬──────────────┬──┴───────────┬──────────────┬──────────────┐
        ▼              ▼              ▼              ▼              ▼              ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
   │ Agent 1 │    │ Agent 2 │    │ Agent 3 │    │ Agent 4 │    │ Agent 5 │    │ Agent 6 │
   │ Repo    │    │ AI      │    │ Exec &  │    │ GitHub  │    │ Actions │    │ State & │
   │ Context │    │ Plan &  │    │ Safety  │    │ REST    │    │ & Ops   │    │ Reports │
   │ AST     │    │ Repair  │    │ Sandbox │    │ Ops     │    │ & CI    │    │ Memory  │
   └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────┘
```

### Milestone Schedule

| Work Package | Lead Agent | Primary Deliverables | Target Status |
| :--- | :--- | :--- | :--- |
| **Package 1: Intelligence** | Agent 1 | Deep AST parsing, framework heuristics, issue context extractor | Ready to Start |
| **Package 2: AI Planning** | Agent 2 | Multi-model free inference, structured patch repair, token accounting | Ready to Start |
| **Package 3: Execution** | Agent 3 | Container/chroot sandboxing, diff validation, lint auto-fix | Ready to Start |
| **Package 4: GitHub Ops** | Agent 4 | Pagination, rate-limit backoff, commit signing, PR template formatting | Ready to Start |
| **Package 5: Actions & Ops** | Agent 5 | Concurrency verification, runner optimizations, operations guide | Ready to Start |
| **Package 6: Persistence** | Agent 6 | Git branch commit syncing, state rollback, metrics dashboard | Ready to Start |

---

## 3. Integration & Rollout Sequence

To ensure stability, parallel agent outputs must be integrated in the following sequence:

1. **Step 1: Intelligence & Ops Verification (Agents 1 & 5)**
   - Merge deep AST inspection from Agent 1.
   - Verify workflow syntax and security permissions from Agent 5.
   - Run tests: `pytest tests/unit/test_intelligence.py`.

2. **Step 2: AI Planning & Sandbox Hardening (Agents 2 & 3)**
   - Merge bounded repair planner from Agent 2.
   - Merge enhanced sandboxing and diff validation from Agent 3.
   - Run tests: `pytest tests/unit/test_ai_provider.py tests/unit/test_execution_security.py`.

3. **Step 3: GitHub Ops & Durable Persistence (Agents 4 & 6)**
   - Merge resilient GitHub REST client from Agent 4.
   - Merge durable Git-branch state store from Agent 6.
   - Run tests: `pytest tests/unit/test_github_client.py tests/unit/test_state.py`.

4. **Step 4: End-to-End Autonomous Simulation**
   - Execute full system simulation in test mode: `repopilot --test-mode`.
   - Run complete test suite: `pytest -v`.

---

## 4. Risk Register and Mitigation

| Risk | Probability | Impact | Mitigation Strategy |
| :--- | :---: | :---: | :--- |
| **AI Inference Rate Limit (HTTP 429)** | High | Low | Exponential backoff (1s, 2s, 4s). On persistent 429, skip AI tasks and record in state store without failing the runner. |
| **Runner Exceeding Free Minutes** | Medium | Medium | Strict 10-minute runtime budget in `BudgetTracker` and 15-minute GitHub Actions job timeout. |
| **Hallucinated or Breaking Patches** | High | High | Mandatory validation check: patches must pass all existing tests before publication. Failed attempts are discarded. |
| **Path Traversal / Secret Leakage** | Low | Critical | `PatchValidator` enforces that all paths reside inside workspace root and checks against `excluded_paths`. Privileged tokens stripped from subprocess environment. |
| **Corrupted State File** | Low | Medium | Atomic file write semantics (`write to .tmp` + `os.replace`). State backup stored on dedicated git branch. |
| **Accidental Auto-Merge of Breaking Code** | Low | Critical | `auto_merge` defaults to `False`. Can only be enabled explicitly per repository, and only triggers if CI passes 100%. |
