"""Resource limits, bounded execution budgets, and output control."""

import logging
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

from repopilot.core.exceptions import ResourceLimitExceededError

logger = logging.getLogger(__name__)

# Try importing resource module (available on POSIX/Linux/macOS)
try:
    import resource
    HAS_RESOURCE_MODULE = True
except ImportError:
    resource = None  # type: ignore
    HAS_RESOURCE_MODULE = False


class ResourceLimits(BaseModel):
    """Configured resource limits for execution and validation."""
    timeout_seconds: int = Field(default=180, ge=1, le=600)
    max_memory_bytes: int = Field(default=1024 * 1024 * 1024)  # 1 GB default
    max_cpu_seconds: int = Field(default=180, ge=5, le=600)
    max_disk_bytes: int = Field(default=500 * 1024 * 1024)  # 500 MB workspace disk limit
    max_changed_files: int = Field(default=25, ge=1, le=100)
    max_file_size_bytes: int = Field(default=1024 * 1024)  # 1 MB max single file size
    max_patch_size_bytes: int = Field(default=2 * 1024 * 1024)  # 2 MB max total patch size
    max_output_bytes: int = Field(default=10 * 1024)  # 10 KB output capture limit


def setup_posix_limits(max_memory_bytes: Optional[int] = None, max_cpu_seconds: Optional[int] = None):
    """Sets POSIX resource limits (CPU and memory) in a child process preexec_fn.
    
    Safe on POSIX environments (Linux / macOS). Fails gracefully if OS limits restrict adjustment.
    """
    if not HAS_RESOURCE_MODULE or resource is None:
        return

    # Set CPU time limit if supported
    if max_cpu_seconds and hasattr(resource, "RLIMIT_CPU"):
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds + 5))
        except (ValueError, OSError) as e:
            logger.debug(f"Could not set RLIMIT_CPU: {e}")

    # Set address space memory limit if supported
    if max_memory_bytes and hasattr(resource, "RLIMIT_AS"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))
        except (ValueError, OSError) as e:
            # On macOS, RLIMIT_AS can sometimes be restricted or unsupported for python subprocesses
            logger.debug(f"Could not set RLIMIT_AS: {e}")


def truncate_output(text: str, max_bytes: int = 10240) -> str:
    """Truncate output text to max_bytes with an explicit notification notice."""
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text

    truncated_bytes = encoded[-max_bytes:]
    decoded = truncated_bytes.decode("utf-8", errors="ignore")
    return f"[Output truncated to {max_bytes} bytes (total {len(encoded)} bytes)]\n... " + decoded


def check_directory_size(dir_path: Path, max_bytes: int) -> int:
    """Calculate total size of files in dir_path and raise ResourceLimitExceededError if exceeded."""
    total_size = 0
    if not dir_path.exists():
        return 0

    for root, _, files in os.walk(dir_path):
        for f in files:
            fp = Path(root) / f
            try:
                if not fp.is_symlink():
                    total_size += fp.stat().st_size
            except OSError:
                pass
            if total_size > max_bytes:
                raise ResourceLimitExceededError(
                    f"Workspace disk usage exceeded limit of {max_bytes} bytes (current: {total_size} bytes)."
                )

    return total_size
