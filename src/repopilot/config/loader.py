"""Configuration loader and validator for RepoPilot."""

import os
from pathlib import Path
from typing import Optional, Union
import yaml
from pydantic import ValidationError

from repopilot.config.models import RepoPilotConfig
from repopilot.core.exceptions import ConfigurationError


DEFAULT_CONFIG_LOCATIONS = [
    Path("config/repos.yaml"),
    Path("config/repos.example.yaml"),
    Path("repos.yaml"),
]


def load_config(config_path: Optional[Union[str, Path]] = None) -> RepoPilotConfig:
    """Load, parse, and strictly validate configuration.

    Order of precedence:
    1. Explicit `config_path` parameter if supplied.
    2. REPOPILOT_CONFIG_PATH environment variable if set.
    3. REPOPILOT_CONFIG_YAML inline YAML content environment variable if set.
    4. Default file locations (`config/repos.yaml`, `config/repos.example.yaml`).
    """
    raw_content: Optional[str] = None
    resolved_path: Optional[Path] = None

    if config_path:
        resolved_path = Path(config_path)
    elif os.environ.get("REPOPILOT_CONFIG_PATH"):
        resolved_path = Path(os.environ["REPOPILOT_CONFIG_PATH"])
    elif os.environ.get("REPOPILOT_CONFIG_YAML"):
        raw_content = os.environ["REPOPILOT_CONFIG_YAML"]
    else:
        for loc in DEFAULT_CONFIG_LOCATIONS:
            if loc.exists() and loc.is_file():
                resolved_path = loc
                break

    if raw_content is None:
        if resolved_path is None or not resolved_path.exists():
            raise ConfigurationError(
                f"Configuration file not found. Checked: {config_path or [str(p) for p in DEFAULT_CONFIG_LOCATIONS]}"
            )
        try:
            raw_content = resolved_path.read_text(encoding="utf-8")
        except Exception as e:
            raise ConfigurationError(f"Failed to read config file at '{resolved_path}': {e}") from e

    try:
        data = yaml.safe_load(raw_content)
    except yaml.YAMLError as e:
        raise ConfigurationError(f"YAML parsing error in configuration: {e}") from e

    if not isinstance(data, dict):
        raise ConfigurationError("Configuration root must be a YAML dictionary/object.")

    try:
        config = RepoPilotConfig.model_validate(data)
    except ValidationError as e:
        error_details = []
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "invalid")
            error_details.append(f"{loc}: {msg}")
        raise ConfigurationError(
            f"Configuration validation failed:\n  " + "\n  ".join(error_details),
            details={"errors": e.errors()}
        ) from e

    return config
