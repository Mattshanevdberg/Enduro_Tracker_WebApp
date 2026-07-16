"""
Pure race form, category, and manual-time parsing helpers.

Functions
---------
normalize_race_form
    Normalize race form fields and convert the local start time to epoch seconds.
select_category
    Select a requested category or fall back to the first available category.
parse_manual_time_epoch
    Parse an optional timezone-naive manual timing value into epoch seconds.

These helpers avoid Flask, SQLAlchemy, and templates while reusing the existing
application time conversion rules and shared rider/race category defaults.
"""

from datetime import datetime

from src.utils.riders import DEFAULT_RIDER_CATEGORIES
from src.utils.time import datetime_to_epoch, iso_to_epoch

DEFAULT_RACE_CATEGORIES = DEFAULT_RIDER_CATEGORIES


def normalize_race_form(values) -> dict:
    """
    Normalize submitted race form values.

    Input Args:
      values: mapping-like object containing the race form fields.

    Output:
      Dictionary containing normalized model values and starts_at_epoch.

    Notes:
      Invalid or incomplete date/time input retains the existing behavior of
      storing no start epoch rather than rejecting the full race form.
    """
    start_date = (values.get("start_date") or "").strip()
    start_time = (values.get("start_time") or "").strip()
    starts_at_epoch = None
    if start_date and start_time:
        try:
            local_start = datetime.strptime(
                f"{start_date} {start_time}",
                "%Y-%m-%d %H:%M",
            )
            starts_at_epoch = datetime_to_epoch(local_start)
        except (TypeError, ValueError):
            starts_at_epoch = None

    return {
        "race_id": (values.get("race_id") or "").strip() or None,
        "name": (values.get("name") or "").strip(),
        "website": (values.get("website") or "").strip() or None,
        "description": (values.get("description") or "").strip() or None,
        "starts_at_epoch": starts_at_epoch,
        "active": values.get("active") == "on",
    }


def select_category(requested, categories=DEFAULT_RACE_CATEGORIES) -> str | None:
    """
    Select a supported category with a stable fallback.

    Input Args:
      requested: raw requested category name.
      categories: ordered iterable of available category names.

    Output:
      Requested category when supported, otherwise the first available category,
      or None when no categories are available.
    """
    available = tuple(categories or ())
    normalized = (requested or "").strip()
    if normalized in available:
        return normalized
    return available[0] if available else None


def parse_manual_time_epoch(value) -> int | None:
    """
    Parse an optional manual race time into UTC epoch seconds.

    Input Args:
      value: timezone-naive ISO datetime string from a datetime-local input.

    Output:
      Epoch seconds, or None when the value is empty.

    Raises:
      ValueError when a non-empty value is invalid or includes timezone data.
    """
    normalized = (value or "").strip()
    if not normalized:
        return None
    return iso_to_epoch(normalized, allow_tz=False)
