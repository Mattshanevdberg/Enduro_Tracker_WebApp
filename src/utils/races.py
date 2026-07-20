"""
Pure race form, route/category-name, and manual-time parsing helpers.

Functions
---------
normalize_race_form
    Normalize race form fields and convert the local start time to epoch seconds.
normalize_route_name
    Trim a submitted descriptive route name.
normalize_category_name
    Trim a submitted race category name.
validate_route_name
    Validate a normalized descriptive route name.
validate_category_name
    Validate a normalized race category name.
parse_positive_id
    Parse a required or optional positive database identifier.
parse_manual_time_epoch
    Parse an optional timezone-naive manual timing value into epoch seconds.

These helpers avoid Flask, SQLAlchemy, and templates while reusing the existing
application time conversion rules.
"""

from datetime import datetime

from src.utils.time import datetime_to_epoch, iso_to_epoch

MAX_ROUTE_NAME_LENGTH = 128
MAX_CATEGORY_NAME_LENGTH = 64


def normalize_route_name(value) -> str:
    """
    Normalize a submitted descriptive route name.

    Input Args:
      value: raw route-name value.

    Output:
      Whitespace-trimmed route name.
    """
    return (value or "").strip()


def normalize_category_name(value) -> str:
    """
    Normalize a submitted race category name.

    Input Args:
      value: raw category-name value.

    Output:
      Whitespace-trimmed category name.
    """
    return (value or "").strip()


def validate_route_name(value: str) -> str | None:
    """
    Validate a normalized descriptive route name.

    Input Args:
      value: normalized route name.

    Output:
      User-facing error string, or None when valid.
    """
    if not value:
        return "Route name is required."
    if len(value) > MAX_ROUTE_NAME_LENGTH:
        return f"Route name must be {MAX_ROUTE_NAME_LENGTH} characters or fewer."
    return None


def validate_category_name(value: str) -> str | None:
    """
    Validate a normalized race category name.

    Input Args:
      value: normalized category name.

    Output:
      User-facing error string, or None when valid.
    """
    if not value:
        return "Category name is required."
    if len(value) > MAX_CATEGORY_NAME_LENGTH:
        return f"Category name must be {MAX_CATEGORY_NAME_LENGTH} characters or fewer."
    return None


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


def parse_positive_id(value, required: bool = False) -> int | None:
    """
    Parse a positive database identifier without any Flask dependency.

    Input Args:
      value: raw identifier value.
      required: whether a missing value is invalid.

    Output:
      Positive integer or None for an allowed missing value.

    Raises:
      ValueError when the value is malformed, non-positive, or required but
      missing.
    """
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError("A selection is required.")
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Selection must be a positive number.") from error
    if parsed < 1:
        raise ValueError("Selection must be a positive number.")
    return parsed


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
