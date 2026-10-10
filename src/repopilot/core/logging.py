"""Structured logging with automatic secret redaction for RepoPilot."""

import json
import logging
import os
import re
import sys
from typing import Any, Dict


# Common patterns for secrets to mask in logs
SECRET_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9_]{36,255}"),                 # Classic GitHub PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{82}"),              # Fine-grained PAT
    re.compile(r"ghs_[A-Za-z0-9_]{36}"),                     # GitHub App Installation token
    re.compile(r"AIzaSy[A-Za-z0-9_\-]{33}"),                 # Google AI / Gemini API key
    re.compile(r"sk-[A-Za-z0-9_\-]{32,100}"),                # OpenAI / Anthropic key
    re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE),  # Authorization Bearer tokens
    re.compile(r"token\s+([A-Za-z0-9_\-\.]+)", re.IGNORECASE),   # Authorization token
]


def redact_secrets(text: str) -> str:
    """Mask known secret patterns with [REDACTED]."""
    if not isinstance(text, str):
        text = str(text)

    # Redact explicit environment variable values if present
    for env_var in ("GITHUB_TOKEN", "GH_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        val = os.environ.get(env_var)
        if val and len(val) >= 8:
            text = text.replace(val, "[REDACTED_SECRET]")

    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_TOKEN]", text)

    return text


class SanitizingFormatter(logging.Formatter):
    """Custom formatter that automatically strips secrets and outputs structured text."""

    def __init__(self, json_output: bool = False):
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        original_msg = record.getMessage()
        sanitized_msg = redact_secrets(original_msg)

        if self.json_output:
            log_data: Dict[str, Any] = {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": sanitized_msg,
            }
            if hasattr(record, "repo"):
                log_data["repo"] = record.repo
            if hasattr(record, "task_id"):
                log_data["task_id"] = record.task_id
            if record.exc_info:
                log_data["exception"] = redact_secrets(self.formatException(record.exc_info))
            return json.dumps(log_data)
        else:
            time_str = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
            prefix = f"[{time_str}] [{record.levelname:<7}] [{record.name}]"
            extra = ""
            if hasattr(record, "repo"):
                extra += f" [repo={record.repo}]"
            if hasattr(record, "task_id"):
                extra += f" [task={record.task_id}]"
            formatted = f"{prefix}{extra} {sanitized_msg}"
            if record.exc_info:
                formatted += "\n" + redact_secrets(self.formatException(record.exc_info))
            return formatted


def setup_logger(name: str = "repopilot", level: str = "INFO", json_mode: bool = False) -> logging.Logger:
    """Set up and configure a structured, sanitizing logger."""
    logger = logging.getLogger(name)
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    # Avoid adding duplicate handlers if already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(SanitizingFormatter(json_output=json_mode))
        logger.addHandler(handler)

    logger.propagate = False
    return logger
