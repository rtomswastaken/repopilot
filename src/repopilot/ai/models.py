"""Structured models for AI planning, repository reasoning, and bounded repair."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from repopilot.core.models import (
    ChangePlan,
    FileChange,
    Task,
    TaskCategory,
    TaskStatus,
)


class StructuredTaskPlan(BaseModel):
    """Rich, structured plan generated during autonomous repository analysis."""

    id: str
    category: TaskCategory
    title: str
    description: str
    problem_or_opportunity: str = ""
    evidence: str = ""
    expected_benefit: str = ""
    relevant_files: List[str] = Field(default_factory=list)
    implementation_steps: List[str] = Field(default_factory=list)
    required_tests: List[str] = Field(default_factory=list)
    risk_classification: str = "low"  # low, medium, high
    estimated_complexity: str = "low"  # trivial, low, medium, high
    validation_criteria: List[str] = Field(default_factory=list)
    abandon_conditions: List[str] = Field(default_factory=list)
    related_issue_number: Optional[int] = None

    def to_core_task(self, repo_owner: str, repo_name: str) -> Task:
        """Convert structured plan into the frozen core Task model."""
        # Format the structured details into the task description for full transparency
        details_md = [
            self.description,
            "",
            f"**Problem / Opportunity**: {self.problem_or_opportunity or 'N/A'}",
            f"**Evidence**: {self.evidence or 'N/A'}",
            f"**Expected Benefit**: {self.expected_benefit or 'N/A'}",
            f"**Risk Level**: {self.risk_classification} | **Estimated Complexity**: {self.estimated_complexity}",
        ]
        if self.implementation_steps:
            details_md.append("\n**Implementation Steps**:")
            details_md.extend([f"- {s}" for s in self.implementation_steps])
        if self.required_tests:
            details_md.append("\n**Required Tests**:")
            details_md.extend([f"- {t}" for t in self.required_tests])
        if self.validation_criteria:
            details_md.append("\n**Validation Criteria**:")
            details_md.extend([f"- {v}" for v in self.validation_criteria])
        if self.abandon_conditions:
            details_md.append("\n**Abandon Conditions**:")
            details_md.extend([f"- {a}" for a in self.abandon_conditions])

        full_desc = "\n".join(details_md).strip()

        return Task(
            id=self.id,
            repo_owner=repo_owner,
            repo_name=repo_name,
            category=self.category,
            title=self.title,
            description=full_desc,
            status=TaskStatus.DISCOVERED,
            related_files=self.relevant_files,
            related_issue_number=self.related_issue_number,
        )


class RepairDiagnosis(BaseModel):
    """Structured analysis and repair decision for a validation failure."""

    can_repair: bool
    reason: str
    likely_cause: str = ""
    error_summary: str = ""
    suggested_files: List[str] = Field(default_factory=list)
    suggested_fix: str = ""
    abandon: bool = False
