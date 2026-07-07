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


def utc_now() -> datetime:
    """
    Return the current UTC datetime.

    Input Args:
      None.

    Output:
      Timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


def as_aware_utc(value: datetime) -> datetime:
    """
    Convert a datetime value to timezone-aware UTC.

    Input Args:
      value: datetime value to normalise.

    Output:
      Timezone-aware UTC datetime.

    Notes:
      PostgreSQL preserves timezone-aware values in the target runtime, while
      lightweight SQLite checks can return naive datetimes. Naive values are
      treated as UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def rfid_timestamp_to_epoch(timestamp_str: str, tz_name: str | None = None) -> int | None:
    """
    Parse an RFID reader timestamp string and return UTC epoch seconds.

    Accepted shapes:
      - 20260526T163756   (reader compact timestamp)
      - 20260526163756    (compact timestamp without "T")
      - ISO8601 strings accepted by datetime.fromisoformat()

    Empty strings return None so callers can decide whether the timestamp is required.
    Timezone-naive timestamps are interpreted in the configured local timezone.
    """
    if not timestamp_str:
        return None

    cleaned = timestamp_str.strip()
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"):
        try:
            return datetime_to_epoch(datetime.strptime(cleaned, fmt), tz_name=tz_name)
        except ValueError:
            pass

    # Accept ISO strings as a fallback, including the common trailing "Z" UTC marker.
    dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    return datetime_to_epoch(dt, tz_name=tz_name)
