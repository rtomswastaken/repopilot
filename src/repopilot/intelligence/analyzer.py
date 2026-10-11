"""Repository intelligence, language detection, and context extraction."""

import ast
from abc import ABC, abstractmethod
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from repopilot.github.models import GitHubIssue

logger = logging.getLogger(__name__)


class RepoContext(BaseModel):
    """Summarized intelligence and metadata about a repository."""
    repo_name: str
    languages: List[str] = Field(default_factory=list)
    frameworks: List[str] = Field(default_factory=list)
    test_frameworks: List[str] = Field(default_factory=list)
    doc_files: List[str] = Field(default_factory=list)
    file_tree: List[str] = Field(default_factory=list)
    readme_content: Optional[str] = None
    open_issues: List[Dict[str, Any]] = Field(default_factory=list)
    ast_summary: Dict[str, Any] = Field(default_factory=dict)


class RepositoryAnalyzerInterface(ABC):
    """Interface for repository context analysis."""

    @abstractmethod
    def analyze(self, workspace_path: Path, issues: Optional[List[GitHubIssue]] = None) -> RepoContext:
        pass


class LocalRepositoryAnalyzer(RepositoryAnalyzerInterface):
    """Analyzes filesystem structure, AST, and technology stack of a repository."""

    def analyze(self, workspace_path: Path, issues: Optional[List[GitHubIssue]] = None) -> RepoContext:
        workspace_path = workspace_path.resolve()
        languages: List[str] = []
        frameworks: List[str] = []
        test_frameworks: List[str] = []
        doc_files: List[str] = []
        file_tree: List[str] = []
        readme_content: Optional[str] = None
        ast_summary: Dict[str, Any] = {
            "parsed_files": 0,
            "functions": 0,
            "classes": 0,
            "syntax_errors": 0,
            "imports": [],
        }

        # Ignore heavy or internal directories
        ignored_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "build", "dist"}

        count = 0
        collected_imports = set()

        for root, dirs, files in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
            for file in files:
                if file.startswith("."):
                    continue
                full_path = Path(root) / file
                try:
                    rel_path = full_path.relative_to(workspace_path).as_posix()
                except ValueError:
                    continue

                count += 1
                if count <= 250:
                    file_tree.append(rel_path)

                lower_file = file.lower()

                # Language detection
                if lower_file in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "pipfile") or file.endswith(".py"):
                    if "Python" not in languages:
                        languages.append("Python")
                if lower_file in ("package.json", "tsconfig.json", "yarn.lock", "pnpm-lock.yaml") or file.endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")):
                    if "JavaScript/TypeScript" not in languages:
                        languages.append("JavaScript/TypeScript")
                if lower_file in ("cargo.toml", "cargo.lock") or file.endswith(".rs"):
                    if "Rust" not in languages:
                        languages.append("Rust")
                if lower_file in ("go.mod", "go.sum") or file.endswith(".go"):
                    if "Go" not in languages:
                        languages.append("Go")
                if lower_file in ("pom.xml", "build.gradle", "build.gradle.kts") or file.endswith((".java", ".kt", ".scala")):
                    if "Java" not in languages:
                        languages.append("Java")
                if lower_file in ("cmakelists.txt", "makefile") or file.endswith((".c", ".cpp", ".cc", ".cxx", ".h", ".hpp")):
                    if "C/C++" not in languages:
                        languages.append("C/C++")

                # Test Framework detection by file pattern
                if "pytest" in lower_file or "test_" in lower_file or "_test.py" in lower_file or "tests.py" in lower_file:
                    if "pytest" not in test_frameworks:
                        test_frameworks.append("pytest")
                if "jest" in lower_file or ".test." in lower_file or ".spec." in lower_file:
                    if "jest" not in test_frameworks:
                        test_frameworks.append("jest")

                # Documentation discovery
                if lower_file.startswith("readme"):
                    doc_files.append(rel_path)
                    if readme_content is None:
                        try:
                            readme_content = full_path.read_text(encoding="utf-8", errors="replace")[:8000]
                        except Exception:
                            pass
                elif lower_file.startswith(("contributing", "license", "changelog", "architecture", "security")):
                    doc_files.append(rel_path)

                # Inspect dependency files for frameworks & test runners
                if lower_file in ("requirements.txt", "pyproject.toml", "setup.py"):
                    try:
                        text = full_path.read_text(encoding="utf-8", errors="ignore").lower()
                        if "pytest" in text and "pytest" not in test_frameworks:
                            test_frameworks.append("pytest")
                        if "unittest" in text and "unittest" not in test_frameworks:
                            test_frameworks.append("unittest")
                        if "django" in text and "Django" not in frameworks:
                            frameworks.append("Django")
                        if "fastapi" in text and "FastAPI" not in frameworks:
                            frameworks.append("FastAPI")
                        if "flask" in text and "Flask" not in frameworks:
                            frameworks.append("Flask")
                        if "pydantic" in text and "Pydantic" not in frameworks:
                            frameworks.append("Pydantic")
                    except Exception:
                        pass

                if lower_file == "package.json":
                    try:
                        text = full_path.read_text(encoding="utf-8", errors="ignore").lower()
                        if "react" in text and "React" not in frameworks:
                            frameworks.append("React")
                        if "express" in text and "Express" not in frameworks:
                            frameworks.append("Express")
                        if "next" in text and "Next.js" not in frameworks:
                            frameworks.append("Next.js")
                        if "vue" in text and "Vue" not in frameworks:
                            frameworks.append("Vue")
                        if "jest" in text and "jest" not in test_frameworks:
                            test_frameworks.append("jest")
                        if "mocha" in text and "mocha" not in test_frameworks:
                            test_frameworks.append("mocha")
                    except Exception:
                        pass

                # Python AST Inspection
                if file.endswith(".py") and full_path.stat().st_size < 100_000:
                    try:
                        source = full_path.read_text(encoding="utf-8", errors="replace")
                        tree = ast.parse(source, filename=str(full_path))
                        ast_summary["parsed_files"] += 1
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                ast_summary["functions"] += 1
                            elif isinstance(node, ast.ClassDef):
                                ast_summary["classes"] += 1
                            elif isinstance(node, ast.Import):
                                for alias in node.names:
                                    top_mod = alias.name.split(".")[0]
                                    collected_imports.add(top_mod)
                            elif isinstance(node, ast.ImportFrom):
                                if node.module:
                                    top_mod = node.module.split(".")[0]
                                    collected_imports.add(top_mod)
                    except SyntaxError:
                        ast_summary["syntax_errors"] += 1
                    except Exception:
                        pass

        # Map collected imports to frameworks
        for mod in collected_imports:
            mod_lower = mod.lower()
            if mod_lower == "django" and "Django" not in frameworks:
                frameworks.append("Django")
            elif mod_lower == "fastapi" and "FastAPI" not in frameworks:
                frameworks.append("FastAPI")
            elif mod_lower == "flask" and "Flask" not in frameworks:
                frameworks.append("Flask")
            elif mod_lower == "pydantic" and "Pydantic" not in frameworks:
                frameworks.append("Pydantic")
            elif mod_lower == "pytest" and "pytest" not in test_frameworks:
                test_frameworks.append("pytest")

        ast_summary["imports"] = sorted(list(collected_imports))[:50]

        issues_summary = []
        if issues:
            for iss in issues:
                issues_summary.append({
                    "number": iss.number,
                    "title": iss.title,
                    "body": iss.body[:500],
                    "labels": iss.labels,
                })

        return RepoContext(
            repo_name=workspace_path.name,
            languages=languages or ["Unknown"],
            frameworks=frameworks,
            test_frameworks=test_frameworks,
            doc_files=doc_files,
            file_tree=file_tree,
            readme_content=readme_content,
            open_issues=issues_summary,
            ast_summary=ast_summary,
        )
