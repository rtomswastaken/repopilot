"""Project-aware test and lint discovery across ecosystems (Python, Node/TS, Go, Rust)."""

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DiscoveredValidation(BaseModel):
    """Result of project inspection for testing and linting capabilities."""
    ecosystem: str = "unknown"
    has_tests: bool = False
    test_command: Optional[str] = None
    lint_command: Optional[str] = None
    discovered_configs: List[str] = Field(default_factory=list)
    test_files_count: int = 0
    advisory_notes: List[str] = Field(default_factory=list)


class TestDiscoverer:
    """Inspects a repository workspace to discover test frameworks, configs, and commands."""

    @classmethod
    def discover(cls, workspace_root: Path) -> DiscoveredValidation:
        """Inspect workspace to detect language ecosystem, existing test commands, and test presence."""
        workspace_root = workspace_root.resolve()

        # 1. Try Node / TypeScript detection
        node_res = cls._inspect_node(workspace_root)
        if node_res:
            return node_res

        # 2. Try Python detection
        py_res = cls._inspect_python(workspace_root)
        if py_res:
            return py_res

        # 3. Try Rust detection
        rust_res = cls._inspect_rust(workspace_root)
        if rust_res:
            return rust_res

        # 4. Try Go detection
        go_res = cls._inspect_go(workspace_root)
        if go_res:
            return go_res

        return DiscoveredValidation(
            ecosystem="unknown",
            has_tests=False,
            test_command=None,
            lint_command=None,
            advisory_notes=["No known language ecosystem or test configuration detected in repository."],
        )

    @classmethod
    def _inspect_python(cls, root: Path) -> Optional[DiscoveredValidation]:
        configs = []
        pyproject = root / "pyproject.toml"
        setup_cfg = root / "setup.cfg"
        pytest_ini = root / "pytest.ini"
        tox_ini = root / "tox.ini"

        if pyproject.exists():
            configs.append("pyproject.toml")
        if setup_cfg.exists():
            configs.append("setup.cfg")
        if pytest_ini.exists():
            configs.append("pytest.ini")
        if tox_ini.exists():
            configs.append("tox.ini")

        # Check for python files
        has_py_files = False
        test_files = []
        for r, _, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    has_py_files = True
                    if f.startswith("test_") or f.endswith("_test.py") or "tests" in r.split(os.sep):
                        test_files.append(f)

        if not has_py_files and not configs:
            return None

        has_tests = len(test_files) > 0 or pytest_ini.exists()
        test_cmd = "pytest" if has_tests else None

        # Lint command detection
        lint_cmd = None
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding="utf-8")
                if "tool.ruff" in content:
                    lint_cmd = "ruff check ."
                elif "tool.flake8" in content:
                    lint_cmd = "flake8"
                elif "tool.mypy" in content:
                    lint_cmd = "mypy ."
            except Exception:
                pass

        notes = []
        if not has_tests:
            notes.append("Python repository detected, but no test files or test runner configuration found.")

        return DiscoveredValidation(
            ecosystem="python",
            has_tests=has_tests,
            test_command=test_cmd,
            lint_command=lint_cmd,
            discovered_configs=configs,
            test_files_count=len(test_files),
            advisory_notes=notes,
        )

    @classmethod
    def _inspect_node(cls, root: Path) -> Optional[DiscoveredValidation]:
        pkg_json_path = root / "package.json"
        if not pkg_json_path.exists():
            return None

        configs = ["package.json"]
        is_ts = (root / "tsconfig.json").exists()
        if is_ts:
            configs.append("tsconfig.json")

        # Determine package manager
        pkg_mgr = "npm"
        if (root / "pnpm-lock.yaml").exists():
            pkg_mgr = "pnpm"
        elif (root / "yarn.lock").exists():
            pkg_mgr = "yarn"
        elif (root / "bun.lockb").exists():
            pkg_mgr = "bun"

        test_cmd: Optional[str] = None
        lint_cmd: Optional[str] = None
        has_tests = False
        notes = []

        try:
            data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})

            if "test" in scripts:
                script_val = scripts["test"]
                # Default npm init test command is a stub failure
                if "no test specified" in script_val:
                    has_tests = False
                    notes.append("package.json test script is an unconfigured default stub.")
                else:
                    has_tests = True
                    test_cmd = f"{pkg_mgr} test" if pkg_mgr != "yarn" else "yarn test"

            if "lint" in scripts:
                lint_cmd = f"{pkg_mgr} run lint" if pkg_mgr != "yarn" else "yarn lint"
            elif is_ts:
                lint_cmd = "npx tsc --noEmit"

        except Exception as e:
            logger.warning(f"Error parsing package.json: {e}")

        # Check for test files if script wasn't definitive
        test_files = []
        if not has_tests:
            for r, _, files in os.walk(root):
                if "node_modules" in r:
                    continue
                for f in files:
                    if f.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts", ".test.jsx", ".test.tsx")):
                        test_files.append(f)
            if test_files:
                has_tests = True
                test_cmd = f"{pkg_mgr} test"

        if not has_tests:
            notes.append("Node.js repository detected, but no tests defined or found.")

        return DiscoveredValidation(
            ecosystem="typescript" if is_ts else "javascript",
            has_tests=has_tests,
            test_command=test_cmd,
            lint_command=lint_cmd,
            discovered_configs=configs,
            test_files_count=len(test_files),
            advisory_notes=notes,
        )

    @classmethod
    def _inspect_rust(cls, root: Path) -> Optional[DiscoveredValidation]:
        cargo = root / "Cargo.toml"
        if not cargo.exists():
            return None

        # Look for rs test files
        test_files = []
        for r, _, files in os.walk(root):
            for f in files:
                if f.endswith(".rs") and ("test" in f or "tests" in r):
                    test_files.append(f)

        return DiscoveredValidation(
            ecosystem="rust",
            has_tests=True,
            test_command="cargo test",
            lint_command="cargo clippy -- -D warnings",
            discovered_configs=["Cargo.toml"],
            test_files_count=len(test_files),
        )

    @classmethod
    def _inspect_go(cls, root: Path) -> Optional[DiscoveredValidation]:
        gomod = root / "go.mod"
        if not gomod.exists():
            return None

        test_files = []
        for r, _, files in os.walk(root):
            for f in files:
                if f.endswith("_test.go"):
                    test_files.append(f)

        has_tests = len(test_files) > 0
        return DiscoveredValidation(
            ecosystem="go",
            has_tests=has_tests,
            test_command="go test ./..." if has_tests else None,
            lint_command="golangci-lint run",
            discovered_configs=["go.mod"],
            test_files_count=len(test_files),
            advisory_notes=[] if has_tests else ["Go module found, but no *_test.go files discovered."],
        )
