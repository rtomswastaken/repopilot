# RepoPilot AI Subsystem: Architecture, Planning & Code Generation

This document details the architecture, configuration, supported providers, security controls, and limitations of RepoPilot's AI reasoning and code generation subsystem (Agent 2).

---

## 1. Subsystem Architecture

The AI subsystem operates under a strict **Zero-Cost (₹0) Invariant**: it executes purely on free-tier inference (Google Gemini Free Tier in AI Studio) or local/offline models (Ollama, LM Studio), with deterministic mock fallbacks for testing.

```
src/repopilot/ai/
├── __init__.py           # Unified exports
├── provider.py           # Core AIProviderInterface, TokenUsage, and request contracts
├── models.py             # StructuredTaskPlan and RepairDiagnosis domain models
├── prioritizer.py        # 6-tier task prioritisation and deduplication
├── validator.py          # SecurityProposalValidator (path traversal, test suppression, secret leaks)
├── repair_planner.py     # Bounded RepairPlanner (log diagnosis, repeat-patch prevention)
├── gemini_provider.py    # Google Gemini Free Tier integration with JSON mode & usage tracking
├── local_provider.py     # Local / OpenAI-compatible zero-cost provider with paid-API blocker
├── mock_provider.py      # Deterministic mock provider with configurable test failure simulation
└── router.py             # Provider instantiation and safe unconfigured degradation
```

---

## 2. Supported AI Providers

### A. Google Gemini Free Tier (`GeminiProvider`)
- **Cost**: ₹0.00 / $0.00 (Google AI Studio Free Tier).
- **Default Model**: `gemini-1.5-flash` (also supports `gemini-2.0-flash`, `gemini-1.5-pro` free tier).
- **Rate Limits & Budgets**: Automatically catches HTTP 429 (`AIRateLimitError`) and HTTP 403 quota exhaustion (`AIQuotaExhaustedError`), updating provider status accordingly.
- **Usage Tracking**: Tracks `prompt_tokens`, `completion_tokens`, and `total_tokens` directly from API metadata while enforcing `estimated_cost_usd = 0.0`.
- **Configuration**:
  ```yaml
  settings:
    ai_provider: gemini
    ai_model: gemini-1.5-flash
    ai_request_timeout_seconds: 30
  ```
  Requires `GEMINI_API_KEY` environment variable.

### B. Local / Self-Hosted Models (`LocalAIProvider`)
- **Cost**: ₹0.00 (Local compute).
- **Supported Backends**: Ollama (`http://localhost:11434/v1`), LM Studio, vLLM, or self-hosted OpenAI-compatible inference servers.
- **Paid Domain Blocker**: Hard blocks requests to known commercial paid API domains (e.g. `api.openai.com`, `api.anthropic.com`, `api.cohere.ai`) with `SecurityViolationError` to prevent accidental billing.
- **Configuration**:
  ```yaml
  settings:
    ai_provider: local
    ai_model: llama3
    ai_request_timeout_seconds: 45
  ```

### C. Deterministic Mock Provider (`MockAIProvider`)
- **Cost**: ₹0.00 (Offline, zero network calls).
- **Usage**: Local test runs (`repopilot --test-mode`), CI test workflows, and offline development.
- **Configurable Edge Cases**: Can simulate rate limits, quota depletion, request timeouts, malformed responses, prohibited proposals, and oversized patches for unit testing.

### D. Disabled / Unconfigured Provider
- If `ai_provider: none` or an unknown provider is specified, the router returns an unconfigured provider with `is_available() == False` rather than silently calling a paid fallback. The orchestrator skips AI tasks cleanly while allowing non-AI maintenance to proceed.

---

## 3. Autonomous Task Planning & Prioritisation

### Structured Planning Schema (`StructuredTaskPlan`)
Every proposed plan contains complete contextual evidence:
- **Task Identity**: `id`, `category`, `title`, `description`.
- **Reasoning**: `problem_or_opportunity`, `evidence`, `expected_benefit`.
- **Action Plan**: `relevant_files`, `implementation_steps`, `required_tests`.
- **Governance**: `risk_classification` (`low`/`medium`/`high`), `estimated_complexity`, `validation_criteria`, `abandon_conditions`.

### Priority Hierarchy
Tasks are prioritised strictly according to project value:
1. **Reproducible bugs** (`TaskCategory.BUG_FIX` — boosted if tied to open GitHub issues).
2. **Missing or inadequate tests** (`TaskCategory.TESTS`).
3. **Clear documentation errors** (`TaskCategory.DOCUMENTATION`).
4. **Safe code-quality improvements** (`TaskCategory.CODE_QUALITY`, `TaskCategory.TYPE_ANNOTATION`).
5. **Compatible dependency maintenance** (`TaskCategory.MAINTENANCE`).
6. **Small features supported by requirements** (`TaskCategory.REFACTOR`).

### Deduplication
`TaskPrioritizer` filters out:
- Duplicate tasks within the same batch.
- Tasks matching signatures in persistent `task_history`.
- Tasks previously recorded in `rejected_tasks`.

---

## 4. Defensive Security Boundaries

All repository context (code, issues, docstrings, commits) is treated as **untrusted input**. Prompt injection cannot grant permission to weaken security or violate boundaries.

1. **Path Traversal Defense**: All proposed file paths are verified against workspace root escaping (`../`, absolute paths).
2. **Excluded Paths Enforcement**: Changes targeting `.github/workflows/`, `secrets/`, `.env`, or repository-configured `excluded_paths` are rejected in application code.
3. **Test Disabling Prevention**: Patches attempting to remove test assertions, comment out tests, or add skip markers (`@pytest.mark.skip`, `unittest.skip`, `xit`, `test.skip`, `// @ts-ignore`) are rejected.
4. **Secret Protection**: Scans for RSA private keys, GitHub tokens, and API credentials in prompts and generated patches; masks secrets via `redact_secrets()`.
5. **Patch Size Bounds**: Enforces maximum files per patch (default 5) and maximum file size budget (100 KB) to prevent sweeping repository rewrites.

---

## 5. Bounded Repair Planning (`RepairPlanner`)

When tests or linters fail during task validation:
1. **Feasibility Evaluation**: Checks if retries remain (`retry_attempt < max_retries`). If limit is reached, terminates with `abandon=True`.
2. **Error Diagnosis**: Parses stderr/stdout to detect specific root causes (`AssertionError`, `ImportError`, `SyntaxError`, pytest failure lines).
3. **Fatal Error Filtering**: Flags environmental issues (e.g. disk full, permission denied, unreachable network) as non-repairable, preventing wasted retries.
4. **Repeat-Patch Prevention**: Fingerprints every attempted patch with SHA-256. If the AI proposes a patch identical to an earlier failed attempt, it is immediately rejected.
5. **Integrity Rule**: Generated code is **never** declared passing based on AI assertion; only actual execution validation confirms success.

---

## 6. Known Limitations

1. **Free Tier Rate Limits**: Google Gemini Free Tier limits request frequency (typically 15 requests/minute). When rate limits occur, RepoPilot backs off or skips AI tasks until the next scheduled run.
2. **Repository Size & Truncation**: Repositories exceeding context window boundaries are sampled via top-level file trees (up to 50 files) and truncated file contents (up to 10 KB per file) to prevent token exhaustion.
3. **Offline Mode**: When internet connectivity is absent, live Gemini inference is unavailable; the system operates via `MockAIProvider` or local models.
