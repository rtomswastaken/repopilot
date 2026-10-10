"""Google Gemini Free Tier inference provider for zero-cost operation."""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from repopilot.ai.provider import (
    AICodeChangeRequest,
    AIPlanRequest,
    AIProviderInterface,
    ProviderStatus,
)
from repopilot.core.exceptions import AIQuotaExhaustedError, AIRateLimitError
from repopilot.core.models import ChangeAction, ChangePlan, FileChange, Task, TaskCategory, TaskStatus

logger = logging.getLogger(__name__)

GEMINI_API_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiProvider(AIProviderInterface):
    """Integrates Google Gemini Free Tier via Google AI Studio API key."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-1.5-flash",
        timeout_seconds: int = 30,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def get_name(self) -> str:
        return "gemini"

    def get_status(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus.UNCONFIGURED
        return ProviderStatus.AVAILABLE

    def _call_api(self, prompt: str) -> str:
        """Call Gemini REST API without external heavyweight dependencies."""
        if not self.api_key:
            raise AIQuotaExhaustedError("GEMINI_API_KEY is not configured.")

        url = f"{GEMINI_API_ENDPOINT.format(model=self.model)}?key={self.api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
                res_body = response.read().decode("utf-8")
                res_json = json.loads(res_body)
                candidates = res_json.get("candidates", [])
                if not candidates:
                    return "{}"
                return candidates[0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                logger.warning("Gemini rate limit exceeded (HTTP 429).")
                raise AIRateLimitError("Gemini rate limit exceeded.") from e
            elif e.code in (401, 403):
                logger.error(f"Gemini authentication or quota failure (HTTP {e.code}).")
                raise AIQuotaExhaustedError(f"Gemini API authentication failed: {e.reason}") from e
            else:
                logger.error(f"Gemini API HTTP Error {e.code}: {e.reason}")
                raise
        except Exception as e:
            logger.error(f"Gemini API request failed: {e}")
            raise

    def plan_improvements(self, request: AIPlanRequest) -> List[Task]:
        """Propose bounded tasks using structured JSON output from Gemini."""
        if not self.is_available():
            logger.info("Gemini provider unconfigured; skipping AI planning.")
            return []

        prompt = (
            f"You are RepoPilot, an autonomous zero-cost code developer.\n"
            f"Analyze repository '{request.repo_owner}/{request.repo_name}'.\n"
            f"Goals: {request.goals}\n"
            f"Allowed Task Types: {request.allowed_tasks}\n"
            f"Files in repository: {request.file_tree[:50]}\n\n"
            f"Respond with a JSON object containing a list of tasks under key 'tasks'.\n"
            f"Each task must have: id, category, title, description, related_files.\n"
            f"Only suggest small, high-confidence, non-breaking improvements."
        )

        try:
            raw_json = self._call_api(prompt)
            data = json.loads(raw_json)
            tasks_data = data.get("tasks", [])
            tasks: List[Task] = []
            for t in tasks_data[:3]:
                cat = t.get("category", "code_quality")
                category = TaskCategory.CODE_QUALITY
                for member in TaskCategory:
                    if member.value == cat:
                        category = member
                        break

                tasks.append(
                    Task(
                        id=t.get("id", f"gemini-task-{len(tasks)+1}"),
                        repo_owner=request.repo_owner,
                        repo_name=request.repo_name,
                        category=category,
                        title=t.get("title", "Improve codebase"),
                        description=t.get("description", ""),
                        status=TaskStatus.DISCOVERED,
                        related_files=t.get("related_files", []),
                    )
                )
            return tasks
        except Exception as e:
            logger.warning(f"Failed to generate plan from Gemini: {e}")
            return []

    def generate_code_change(self, request: AICodeChangeRequest) -> Optional[ChangePlan]:
        """Generate concrete file changes using Gemini."""
        if not self.is_available():
            return None

        task = request.task
        prompt = (
            f"You are RepoPilot. Implement task: {task.title}\n"
            f"Description: {task.description}\n"
            f"Target files & contents: {request.file_contents}\n"
            f"Errors if retrying: {request.validation_errors or 'None'}\n\n"
            f"Respond with a JSON object: "
            f'{{"files": [{{"path": "relative/path", "action": "modify", "content": "full new content", "rationale": "why"}}]}}'
        )

        try:
            raw_json = self._call_api(prompt)
            data = json.loads(raw_json)
            files = []
            for f in data.get("files", []):
                act_str = f.get("action", "modify").lower()
                action = ChangeAction.CREATE if act_str == "create" else ChangeAction.MODIFY
                files.append(
                    FileChange(
                        path=f["path"],
                        action=action,
                        content=f.get("content", ""),
                        rationale=f.get("rationale", ""),
                    )
                )

            return ChangePlan(
                task_id=task.id,
                title=task.title,
                description=task.description,
                files=files,
            )
        except Exception as e:
            logger.warning(f"Failed to generate code change from Gemini: {e}")
            return None
