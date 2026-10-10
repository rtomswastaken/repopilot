# RepoPilot: Security Policy & Threat Model

Security is a primary design pillar of RepoPilot. Because autonomous systems execute code and generate pull requests without human pre-screening, RepoPilot treats **all repository source code, issues, PR comments, and AI outputs as untrusted inputs**.

---

## 1. Core Security Principles & Invariants

### A. Non-Bypassable Code Boundaries
RepoPilot does **not** rely on AI system prompts as a security boundary. All access controls, path restrictions, and validation checks are strictly implemented in deterministic Python application code.

### B. Prohibited File Modifications
Proposed patches are checked against mandatory excluded paths. The following files and directories can **never** be created, modified, or deleted:
- `.github/workflows/` (GitHub Actions workflow configurations)
- `secrets/`
- `production_credentials/`
- `.env` and `.env.*`
- `*.pem`, `*.key`, `*.p12`
- Credentials and private keys

Attempts to alter prohibited targets trigger an immediate `SecurityViolationError`, terminating the task and recording a security rejection.

### C. Path Traversal Defense
`PatchValidator` enforces that all target paths resolve strictly within the isolated workspace root directory. Relative parent path traversal (`../`) and absolute paths (`/etc/passwd`, `C:\...`) are unconditionally blocked.

### D. Credential Sanitization
1. **Log Sanitization**: `SanitizingFormatter` and `redact_secrets()` scan all log output for tokens matching GitHub PATs (`ghp_`, `github_pat_`, `ghs_`), API keys, and `Bearer` tokens, masking them with `[REDACTED_TOKEN]`.
2. **Subprocess Sanitization**: `ValidationRunner` strips `GITHUB_TOKEN`, `GH_TOKEN`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, and cloud provider credentials before running tests or linters, preventing malicious repository test scripts from exfiltrating secrets.

### E. Auto-Merge Safety Gates
- Auto-merge is **disabled (`false`) by default** for every repository.
- Auto-merge can only be enabled explicitly per repository in `repos.yaml`.
- Auto-merge is **strictly forbidden** if any test or lint check fails.
- PRs touching sensitive files or workflows cannot be auto-merged under any circumstances.

---

## 2. Threat Modeling & Mitigations

| Threat | Risk Level | Defense Mechanism |
| :--- | :---: | :--- |
| **Prompt Injection via GitHub Issues** | High | Issues are parsed as read-only metadata. AI code generation is constrained to typed JSON file changes and executed only within an ephemeral sandbox. |
| **Malicious Test Suite Exfiltration** | High | Privileged tokens (`GITHUB_TOKEN`, API keys) are deleted from the subprocess environment prior to test execution. |
| **Escaping Workspace via Symlinks** | Medium | `PatchValidator` resolves real paths (`Path.resolve()`) and verifies relative containment inside the workspace root. |
| **Infinite Runner Loop / Quota Drain** | Medium | Strict multi-level timeouts: 15m workflow timeout, 10m budget timeout, and per-command process timeouts. |
| **Race Conditions between Workflows** | Medium | GitHub Actions concurrency groups (`repopilot-execution`) with `cancel-in-progress: false` ensure only one execution runs at a time. |

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability in RepoPilot, please **do not open a public GitHub issue**. Instead, submit a report via GitHub Private Vulnerability Reporting or contact the maintainers directly.
