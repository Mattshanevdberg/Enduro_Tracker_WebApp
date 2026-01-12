"""
Timezone-aware helpers for converting between datetime and epoch seconds.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../../configs/config.yaml")


def _load_timezone_name() -> str:
    """
    Read the configured timezone name from config.yaml.
    Falls back to UTC if missing or unreadable.
    """
    try:
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}
        return (config.get("global", {}) or {}).get("timezone") or config.get("timezone") or "UTC"
    except Exception:
        return "UTC"


def _get_timezone(tz_name: str | None) -> timezone:
    """
    Resolve a timezone name to a tzinfo object, defaulting to UTC on failure.
    """
    name = tz_name or _load_timezone_name()
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def datetime_to_epoch(dt: datetime, tz_name: str | None = None) -> int:
    """
    Convert a datetime to UTC epoch seconds.

    If dt is timezone-naive, it is assumed to be in the configured local timezone
    (or tz_name if provided). If dt is timezone-aware, it is converted to UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_get_timezone(tz_name))
    return int(dt.astimezone(timezone.utc).timestamp())


def epoch_to_datetime(epoch: int | float, tz_name: str | None = None) -> datetime:
    """
    Convert epoch seconds to a timezone-aware datetime in the local timezone.
    """
    tz = _get_timezone(tz_name)
    return datetime.fromtimestamp(float(epoch), tz=tz)


def iso_to_epoch(iso_str: str, tz_name: str | None = None, allow_tz: bool = False) -> int | None:
    """
    Parse an ISO8601 datetime string and return UTC epoch seconds.

    Behavior:
      - Empty strings return None (caller can treat as "clear").
      - If allow_tz is False, timezone-aware inputs are rejected.
      - Timezone-naive inputs are interpreted in the configured local timezone.
    """
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is not None and not allow_tz:
        raise ValueError("timezone offsets are not allowed for this input")
    return datetime_to_epoch(dt, tz_name=tz_name)
