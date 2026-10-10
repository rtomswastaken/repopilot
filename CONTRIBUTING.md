# Contributing to RepoPilot

Thank you for contributing to RepoPilot! We welcome contributions from both human developers and parallel autonomous AI agents.

---

## 1. Zero-Cost Operating Guideline

All code submitted to RepoPilot must respect the **Zero-Cost Operating Rule (₹0)**:
- Do **not** add dependencies on paid APIs or paid cloud infrastructure.
- All AI features must support free inference tiers (e.g. Google Gemini Free Tier) or deterministic offline mocks.
- Core operations must run within standard GitHub Actions free runner limits.

---

## 2. Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/repo-pilot.git
   cd repo-pilot
   ```

2. **Set up a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -e ".[dev]"
   ```

3. **Run the test suite:**
   ```bash
   pytest -v
   ```

4. **Run RepoPilot in Local Test Mode:**
   ```bash
   repopilot --test-mode --config config/repos.example.yaml
   ```
   *(Local test mode requires no GitHub tokens or AI keys and performs zero external network writes).*

---

## 3. Parallel Agent Architecture & Contracts

RepoPilot is designed for development across six independent work packages.
Before making changes, consult:
- `docs/AGENT_CONTRACTS.md` — Defines exact module ownership, forbidden files, and required tests.
- `docs/IMPLEMENTATION_PLAN.md` — Integration schedule and risk register.

### Golden Rules for Parallel Agents:
- **Do not modify files outside your assigned package.**
- **Do not modify core domain models in `src/repopilot/core/` without backward compatibility.**
- **Always write unit tests for new methods in `tests/unit/`.**
- **Verify all 24+ baseline tests pass before submitting changes.**

---

## 4. Code Quality & Standards

- **Typing**: Use standard Python type hints (`typing` / built-ins).
- **Validation**: Use Pydantic models for structured schemas and data transfer objects.
- **Logging**: Use `setup_logger()` with secret masking; never use raw `print()` for system operations.
- **Exceptions**: Inherit from `RepoPilotError` in `repopilot.core.exceptions`.
