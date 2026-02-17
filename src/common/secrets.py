"""
Lightweight secrets resolution for configuration values.
Supports reading secrets from files (Docker secrets, Kubernetes mounted secrets)
and environment variables.

Usage:
    resolve_secret("file:/run/secrets/redis_password")  -> reads file content
    resolve_secret("env:MY_SECRET_VAR")                 -> reads env variable
    resolve_secret("my-plain-password")                 -> returns as-is
"""
import os
from pathlib import Path
from typing import Optional

from src.common.logging_config import get_logger

logger = get_logger(__name__)


def resolve_secret(value: Optional[str]) -> Optional[str]:
    """
    Resolve a secret value from its reference.

    Supported schemes:
        - ``file:<path>`` — read the secret from a file (trailing newline stripped)
        - ``env:<VAR_NAME>`` — read the secret from an environment variable
        - anything else — returned as-is (plain text)

    Args:
        value: Secret reference string, or None

    Returns:
        Resolved secret value, or None if input is None

    Raises:
        FileNotFoundError: If ``file:`` path does not exist
        KeyError: If ``env:`` variable is not set
    """
    if value is None:
        return None

    if value.startswith("file:"):
        file_path = value[5:]
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Secret file not found: {file_path}")
        secret = path.read_text(encoding="utf-8").strip()
        logger.debug(f"Secret resolved from file: {file_path}")
        return secret

    if value.startswith("env:"):
        var_name = value[4:]
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise KeyError(f"Environment variable not set: {var_name}")
        logger.debug(f"Secret resolved from env: {var_name}")
        return env_value

    # Plain text — return as-is
    return value
