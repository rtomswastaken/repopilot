"""Bounded repair planner for diagnosing validation failures and directing corrective patches."""

import hashlib
import logging
import re
from typing import List, Optional, Set

from repopilot.ai.models import RepairDiagnosis
from repopilot.core.models import ChangePlan, Task

logger = logging.getLogger(__name__)

# Patterns identifying specific failure modes
ERROR_PATTERNS = {
    "AssertionError": re.compile(r"AssertionError(?::\s*(.*))?", re.IGNORECASE),
    "ImportError": re.compile(r"(?:ModuleNotFoundError|ImportError):\s*(.*)", re.IGNORECASE),
    "SyntaxError": re.compile(r"SyntaxError:\s*(.*)", re.IGNORECASE),
    "TypeError": re.compile(r"TypeError:\s*(.*)", re.IGNORECASE),
    "NameError": re.compile(r"NameError:\s*(.*)", re.IGNORECASE),
    "AttributeError": re.compile(r"AttributeError:\s*(.*)", re.IGNORECASE),
    "PytestFailure": re.compile(r"FAILED\s+([^\s:]+)::([^\s]+)", re.IGNORECASE),
}

# Errors that cannot be resolved via repo-level code patches (e.g. system permissions, network)
FATAL_UNREPAIRABLE_PATTERNS = [
    re.compile(r"No space left on device", re.IGNORECASE),
    re.compile(r"Permission denied", re.IGNORECASE),
    re.compile(r"Connection refused", re.IGNORECASE),
    re.compile(r"Network is unreachable", re.IGNORECASE),
]


class RepairPlanner:
    """Diagnoses test and lint failures and regulates bounded repair iterations."""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def evaluate_repair(
        self,
        task: Task,
        validation_errors: Optional[str],
        retry_attempt: int,
        previous_patches: Optional[List[ChangePlan]] = None,
    ) -> RepairDiagnosis:
        """Analyze failure logs and determine whether a bounded repair attempt is justified."""
        previous_patches = previous_patches or []

        # 1. Budget enforcement: stop if retry budget reached
        if retry_attempt >= self.max_retries or retry_attempt >= task.max_retries:
            msg = f"Retry budget exhausted ({retry_attempt} attempts made, limit is {self.max_retries})."
            logger.info(f"Task {task.id}: {msg}")
            return RepairDiagnosis(
                can_repair=False,
                reason=msg,
                abandon=True,
            )

        # 2. Check for missing validation errors
        if not validation_errors or not validation_errors.strip():
            return RepairDiagnosis(
                can_repair=False,
                reason="No failure information or logs available to diagnose.",
                abandon=True,
            )

        # 3. Check for unrepairable system/network errors
        for fatal_pat in FATAL_UNREPAIRABLE_PATTERNS:
            if fatal_pat.search(validation_errors):
                msg = "Failure caused by external system or environmental issue outside repository code scope."
                logger.warning(f"Task {task.id}: {msg}")
                return RepairDiagnosis(
                    can_repair=False,
                    reason=msg,
                    abandon=True,
                )

        # 4. Extract targeted diagnoses
        likely_cause = "General test or lint validation failure"
        suggested_files: Set[str] = set(task.related_files)

        for err_name, pat in ERROR_PATTERNS.items():
            match = pat.search(validation_errors)
            if match:
                likely_cause = f"Detected {err_name}: {match.group(0).strip()}"
                if err_name == "PytestFailure":
                    test_file = match.group(1)
                    suggested_files.add(test_file)
                break

        # Summary of error output (truncated to prevent context blowout)
        error_lines = [l.strip() for l in validation_errors.strip().splitlines() if l.strip()]
        relevant_tail = "\n".join(error_lines[-20:]) if len(error_lines) > 20 else "\n".join(error_lines)

        return RepairDiagnosis(
            can_repair=True,
            reason="Actionable validation failure diagnosed.",
            likely_cause=likely_cause,
            error_summary=relevant_tail,
            suggested_files=sorted(list(suggested_files)),
            suggested_fix=f"Resolve {likely_cause} without modifying unrelated files or disabling tests.",
            abandon=False,
        )

    @staticmethod
    def compute_patch_fingerprint(plan: ChangePlan) -> str:
        """Generate a deterministic fingerprint hash of a proposed ChangePlan."""
        hasher = hashlib.sha256()
        for fc in sorted(plan.files, key=lambda f: f.path):
            hasher.update(fc.path.encode("utf-8"))
            hasher.update(fc.action.value.encode("utf-8"))
            hasher.update((fc.content or "").encode("utf-8"))
        return hasher.hexdigest()

    def is_repeated_patch(self, new_plan: ChangePlan, previous_patches: List[ChangePlan]) -> bool:
        """Verify that the newly proposed patch is not identical to a previously failed patch."""
        if not previous_patches:
            return False

        new_hash = self.compute_patch_fingerprint(new_plan)
        for prev in previous_patches:
            if self.compute_patch_fingerprint(prev) == new_hash:
                logger.warning(f"Proposed patch for task {new_plan.task_id} is identical to previous failed attempt.")
                return True
        return False

    def build_repair_instruction(
        self,
        diagnosis: RepairDiagnosis,
        retry_attempt: int,
    ) -> str:
        """Construct structured guidance for the AI repair iteration."""
        lines = [
            f"=== REPAIR ATTEMPT {retry_attempt + 1} OF {self.max_retries} ===",
            f"Diagnosis: {diagnosis.likely_cause}",
            f"Suggested Focus Files: {', '.join(diagnosis.suggested_files) if diagnosis.suggested_files else 'N/A'}",
            "",
            "Recent Failure Logs:",
            diagnosis.error_summary,
            "",
            "MANDATORY REPAIR RULES:",
            "1. You MUST modify code to specifically fix the root cause diagnosed above.",
            "2. DO NOT propose the same patch that was previously attempted.",
            "3. DO NOT remove, skip, or disable any tests to pass validation.",
            "4. Keep the patch minimal and directly targeted at the error.",
        ]
        return "\n".join(lines)
