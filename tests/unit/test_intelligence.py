"""Unit tests for repository intelligence, AST analysis, framework and language detection."""

from pathlib import Path
import tempfile
import pytest

from repopilot.github.models import GitHubIssue
from repopilot.intelligence.analyzer import LocalRepositoryAnalyzer, RepoContext


def test_python_project_detection():
    analyzer = LocalRepositoryAnalyzer()
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "pyproject.toml").write_text("""
[project]
name = "sample-app"
dependencies = ["pydantic", "fastapi", "pytest"]
""", encoding="utf-8")
        src_dir = root / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("""
import fastapi
from pydantic import BaseModel

class User(BaseModel):
    name: str

def greet(user: User) -> str:
    return f"Hello, {user.name}"
""", encoding="utf-8")

        test_dir = root / "tests"
        test_dir.mkdir()
        (test_dir / "test_main.py").write_text("""
import pytest
from src.main import User, greet

def test_greet():
    assert greet(User(name="Alice")) == "Hello, Alice"
""", encoding="utf-8")

        context = analyzer.analyze(root)
        assert "Python" in context.languages
        assert "FastAPI" in context.frameworks or "Pydantic" in context.frameworks
        assert "pytest" in context.test_frameworks
        assert context.ast_summary["functions"] >= 2
        assert context.ast_summary["classes"] >= 1
        assert "fastapi" in context.ast_summary["imports"] or "pydantic" in context.ast_summary["imports"]


def test_nodejs_project_detection():
    analyzer = LocalRepositoryAnalyzer()
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "package.json").write_text("""{
  "name": "sample-node-app",
  "dependencies": {
    "react": "^18.0.0",
    "express": "^4.18.0"
  },
  "devDependencies": {
    "jest": "^29.0.0"
  }
}""", encoding="utf-8")
        (root / "index.js").write_text("console.log('hello');", encoding="utf-8")
        (root / "index.test.js").write_text("test('dummy', () => {});", encoding="utf-8")

        context = analyzer.analyze(root)
        assert "JavaScript/TypeScript" in context.languages
        assert "React" in context.frameworks
        assert "Express" in context.frameworks
        assert "jest" in context.test_frameworks


def test_multi_language_detection():
    analyzer = LocalRepositoryAnalyzer()
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "Cargo.toml").write_text('[package]\nname = "rust-core"', encoding="utf-8")
        (root / "go.mod").write_text("module example.com/go-svc", encoding="utf-8")
        (root / "pom.xml").write_text("<project></project>", encoding="utf-8")
        (root / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)", encoding="utf-8")

        context = analyzer.analyze(root)
        assert "Rust" in context.languages
        assert "Go" in context.languages
        assert "Java" in context.languages
        assert "C/C++" in context.languages


def test_documentation_discovery():
    analyzer = LocalRepositoryAnalyzer()
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        (root / "README.md").write_text("# My Awesome Project\n\nSetup guide...", encoding="utf-8")
        (root / "CONTRIBUTING.md").write_text("Please follow guidelines.", encoding="utf-8")
        (root / "SECURITY.md").write_text("Security policies.", encoding="utf-8")

        context = analyzer.analyze(root)
        assert "README.md" in context.doc_files
        assert "CONTRIBUTING.md" in context.doc_files
        assert "SECURITY.md" in context.doc_files
        assert context.readme_content is not None
        assert "My Awesome Project" in context.readme_content


def test_issue_context_extraction():
    analyzer = LocalRepositoryAnalyzer()
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        issues = [
            GitHubIssue(
                number=42,
                title="Bug: parser crash on empty string",
                body="Passing empty string raises unhandled IndexError.",
                labels=["bug", "high-priority"],
            )
        ]
        context = analyzer.analyze(root, issues=issues)
        assert len(context.open_issues) == 1
        assert context.open_issues[0]["number"] == 42
        assert context.open_issues[0]["title"] == "Bug: parser crash on empty string"
        assert "IndexError" in context.open_issues[0]["body"]
        assert "bug" in context.open_issues[0]["labels"]


def test_empty_repository_handling():
    analyzer = LocalRepositoryAnalyzer()
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        context = analyzer.analyze(root)
        assert context.languages == ["Unknown"]
        assert context.frameworks == []
        assert context.file_tree == []
        assert context.open_issues == []
