"""Task prioritisation, ordering, and deduplication filtering for autonomous planning."""

import logging
from typing import List, Optional, Set
from repopilot.core.models import Task, TaskCategory

logger = logging.getLogger(__name__)

# Category priority ranking (lower number = higher priority)
CATEGORY_PRIORITY = {
    TaskCategory.BUG_FIX: 1,
    TaskCategory.TESTS: 2,
    TaskCategory.DOCUMENTATION: 3,
    TaskCategory.CODE_QUALITY: 4,
    TaskCategory.TYPE_ANNOTATION: 5,
    TaskCategory.MAINTENANCE: 6,
    TaskCategory.REFACTOR: 7,
}


class TaskPrioritizer:
    """Ranks and filters proposed tasks according to RepoPilot governance guidelines."""

    @staticmethod
    def rank_task(task: Task) -> int:
        """Calculate numerical rank for sorting (lower is higher priority)."""
        base_rank = CATEGORY_PRIORITY.get(task.category, 10) * 100

        # High priority boost for tasks tied to real GitHub issues / bug reports
        if task.related_issue_number is not None:
            base_rank -= 50

        # Priority boost for reproducible bugs
        if task.category == TaskCategory.BUG_FIX:
            base_rank -= 20

        return base_rank

    @classmethod
    def filter_and_prioritize(
        cls,
        tasks: List[Task],
        allowed_categories: Optional[List[str]] = None,
        task_history_signatures: Optional[Set[str]] = None,
        rejected_task_signatures: Optional[Set[str]] = None,
    ) -> List[Task]:
        """Filter out duplicates, rejected tasks, and disallowed categories; then order by priority."""
        history = task_history_signatures or set()
        rejected = rejected_task_signatures or set()
        allowed = set(allowed_categories) if allowed_categories else {c.value for c in TaskCategory}

        filtered: List[Task] = []
        seen_in_current_batch: Set[str] = set()

        for t in tasks:
            cat_str = t.category.value if isinstance(t.category, TaskCategory) else str(t.category)
            if cat_str not in allowed:
                logger.debug(f"Filtering out task '{t.title}': category '{cat_str}' not allowed.")
                continue

            # Normalized signature for deduplication
            sig = f"{t.title.strip().lower()}::{sorted(t.related_files)}"

            if sig in history or t.title.strip().lower() in history:
                logger.info(f"Filtering out duplicate task from history: '{t.title}'")
                continue

            if sig in rejected or t.title.strip().lower() in rejected:
                logger.info(f"Filtering out previously rejected task: '{t.title}'")
                continue

            if sig in seen_in_current_batch:
                logger.debug(f"Filtering out duplicate in current batch: '{t.title}'")
                continue

            seen_in_current_batch.add(sig)
            filtered.append(t)

        # Sort tasks by priority rank
        filtered.sort(key=cls.rank_task)
        return filtered
