# ✈️ RepoPilot

> **Autonomous, zero-cost (₹0) GitHub repository management and development agent.**

RepoPilot is a GitHub-hosted autonomous AI developer that manages multiple public and private GitHub repositories automatically. Once configured, it operates entirely within GitHub Actions scheduled workloads — requiring no daily human intervention, no local machine to be left running, and **₹0 in operating costs**.

---

## 🌟 Key Capabilities

- 🤖 **Genuine Autonomy**: Discovers repositories, inspects code and open issues, proposes concrete improvements, writes tests and documentation, and opens pull requests.
- 💰 **Zero-Cost Operation (₹0)**: Built strictly for free-tier infrastructure — GitHub Actions free runners, free Google Gemini inference, and Git-native branch storage.
- 🛡️ **Defensive Security Boundaries**: Untrusted code is isolated in ephemeral sandboxes. Privileged credentials are scrubbed before running tests. Strict path-traversal prevention and non-bypassable forbidden-path rules protect sensitive files (`.github/workflows/`, secrets, `.env`).
- 🔄 **Fault Isolation**: If one repository encounters an error, execution continues seamlessly across remaining repositories.
- 🧠 **Persistent Memory**: Retains task history, retry counts, and deduplication records across runner teardowns using a dedicated Git orphan state branch (`repopilot-state`).
- ⚡ **Local Test Mode**: Fully testable offline on local machines with zero credentials, zero network requests, and zero external side effects.

---

## 📁 Repository Structure

```
RepoPilot/
├── README.md                      # Project overview & quickstart
├── ARCHITECTURE.md                # In-depth architectural blueprint & lifecycle
├── SECURITY.md                    # Trust boundaries & threat model
├── CONTRIBUTING.md                # Development standards & guidelines
├── pyproject.toml                 # Package configuration & entrypoints
├── .gitignore                     # Git ignore rules
├── config/
│   └── repos.example.yaml         # Example multi-repository configuration
├── docs/
│   ├── AGENT_CONTRACTS.md         # Contracts for 6 parallel engineering agents
│   ├── IMPLEMENTATION_PLAN.md     # Multi-phase roadmap & integration sequence
│   └── OPERATIONS.md              # Deployment, secrets, and disaster recovery
├── src/
│   └── repopilot/
│       ├── __init__.py
│       ├── main.py                # Application entrypoint & CLI
│       ├── config/                # Pydantic schema validation & loader
│       ├── core/                  # Domain models, exceptions, sanitizing logger
│       ├── github/                # REST API client & offline mock client
│       ├── intelligence/          # AST analysis & tech stack detection
│       ├── ai/                    # Pluggable AI provider abstraction (Gemini / Mock)
│       ├── execution/             # Sandboxed workspace & patch applier
│       ├── validation/            # Automated test & lint runner
│       ├── orchestration/         # 17-step lifecycle & budget enforcement
│       ├── state/                 # Persistent state stores (Git branch / atomic file)
│       └── reporting/             # GitHub Step Summary & JSON reporter
├── tests/
│   ├── unit/                      # Unit test suite (Config, State, AI, Security, etc.)
│   └── integration/               # End-to-end autonomous lifecycle simulation
└── .github/
    └── workflows/
        ├── scheduled.yaml         # Scheduled runner (cron: twice daily)
        ├── manual-recovery.yaml   # Manual dispatch & dry runs
        └── test.yaml              # Continuous integration test suite
```

---

## 🚀 Quick Start (Local Test Mode)

RepoPilot includes a comprehensive local test mode that allows full end-to-end execution without needing GitHub tokens or AI API keys:

1. **Clone and enter repository:**
   ```bash
   git clone https://github.com/your-username/repo-pilot.git
   cd repo-pilot
   ```

2. **Create virtual environment and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

3. **Run tests:**
   ```bash
   pytest -v
   ```

4. **Run RepoPilot in Test Mode:**
   ```bash
   repopilot --test-mode --config config/repos.example.yaml
   ```

---

## ⚙️ Configuration (`config/repos.yaml`)

Manage target repositories declaratively:

```yaml
version: 1
settings:
  max_repositories_per_run: 5
  max_tasks_per_repository: 2
  max_retries_per_task: 2
  max_execution_minutes: 10
  max_ai_requests_per_run: 20
  ai_provider: gemini
  ai_model: gemini-1.5-flash

repositories:
  - owner: YOUR_GITHUB_USERNAME
    name: YOUR_REPOSITORY
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
    validation:
      test_command: "pytest tests/"
    publish_mode: pull_request
    auto_merge: false  # Must strictly default to false
```

---

## 🤖 Parallel Agent Work Packages

RepoPilot's foundation is built with frozen core interfaces, ready for six parallel engineering agents to work simultaneously:

- **Agent 1 — Repository Intelligence**: AST analysis, framework heuristics, issue context extraction.
- **Agent 2 — AI Planning & Development**: Gemini free-tier inference, structured code generation, bounded repair planner.
- **Agent 3 — Execution & Validation**: Sandbox hardening, patch verification, linter and test runners.
- **Agent 4 — GitHub Operations**: REST API pagination, branch and commit creation, PR publishing.
- **Agent 5 — GitHub Actions & Operations**: Workflow optimization, concurrency locks, deployment guides.
- **Agent 6 — Persistence & Reporting**: Git-branch state synchronization, duplicate task deduplication, metrics summaries.

See [`docs/AGENT_CONTRACTS.md`](docs/AGENT_CONTRACTS.md) and [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) for full contract specifications.

---

## 📄 License

Apache License 2.0. See [LICENSE](LICENSE) for details.
