"""
Environment-variable parsing helpers.

These utilities keep repeated environment parsing out of application entrypoints
and route modules. They intentionally return explicit defaults for missing or
unrecognised values so each caller can choose a safe fallback.
"""

import os


def env_bool(name: str, default: bool = False) -> bool:
    """
    Parse an environment variable into a boolean value.

    Input Args:
      name: environment variable name to read.
      default: fallback value when the environment variable is missing.

    Output:
      True for common truthy strings, False for common falsey strings, otherwise
      the provided default.
    """
    value = (os.environ.get(name) or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def required_env(name: str, purpose: str = "application configuration") -> str:
    """
    Read a required environment variable and fail clearly when it is missing.

    Input Args:
      name: environment variable name to read.
      purpose: short description of why the value is required. This keeps error
      messages useful without exposing the value itself.

    Output:
      Stripped environment variable value.

    Raises:
      RuntimeError when the variable is missing or blank.
    """
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for {purpose}.")
    return value
