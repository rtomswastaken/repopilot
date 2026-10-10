"""Repository intelligence, language detection, and context extraction."""

from abc import ABC, abstractmethod
import logging
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


class RepositoryAnalyzerInterface(ABC):
    """Interface for repository context analysis."""

    @abstractmethod
    def analyze(self, workspace_path: Path, issues: Optional[List[GitHubIssue]] = None) -> RepoContext:
        pass


class LocalRepositoryAnalyzer(RepositoryAnalyzerInterface):
    """Analyzes filesystem structure and technology stack of a repository."""

    def analyze(self, workspace_path: Path, issues: Optional[List[GitHubIssue]] = None) -> RepoContext:
        workspace_path = workspace_path.resolve()
        languages: List[str] = []
        frameworks: List[str] = []
        test_frameworks: List[str] = []
        doc_files: List[str] = []
        file_tree: List[str] = []
        readme_content: Optional[str] = None

        # Ignore heavy or internal directories
        ignored_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "build", "dist"}

        import os
        count = 0
        for root, dirs, files in os.walk(workspace_path):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
            for file in files:
                if file.startswith("."):
                    continue
                full_path = Path(root) / file
                rel_path = full_path.relative_to(workspace_path).as_posix()
                count += 1
                if count <= 150:
                    file_tree.append(rel_path)

                # Language & Tech detection signals
                lower_file = file.lower()
                if lower_file in ("pyproject.toml", "requirements.txt", "setup.py") or file.endswith(".py"):
                    if "Python" not in languages:
                        languages.append("Python")
                if lower_file == "package.json" or file.endswith((".js", ".ts", ".jsx", ".tsx")):
                    if "JavaScript/TypeScript" not in languages:
                        languages.append("JavaScript/TypeScript")
                if lower_file == "cargo.toml" or file.endswith(".rs"):
                    if "Rust" not in languages:
                        languages.append("Rust")
                if lower_file == "go.mod" or file.endswith(".go"):
                    if "Go" not in languages:
                        languages.append("Go")

                # Test Framework detection
                if "pytest" in lower_file or "test_" in lower_file or "_test.py" in lower_file:
                    if "pytest" not in test_frameworks:
                        test_frameworks.append("pytest")
                if "jest" in lower_file or ".test." in lower_file:
                    if "jest" not in test_frameworks:
                        test_frameworks.append("jest")

                # Documentation discovery
                if lower_file.startswith("readme"):
                    doc_files.append(rel_path)
                    if readme_content is None:
                        try:
                            readme_content = full_path.read_text(encoding="utf-8")[:5000]
                        except Exception:
                            pass
                elif lower_file.startswith(("contributing", "license", "changelog")):
                    doc_files.append(rel_path)

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
        )
