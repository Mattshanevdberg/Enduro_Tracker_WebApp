"""
Pure race form, route/category-name, and manual-time parsing helpers.

Functions
---------
normalize_race_form
    Normalize race metadata, lifecycle, static image filename, and start/end times.
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

from src.utils.media import normalize_static_image_filename
from src.utils.time import datetime_to_epoch, iso_to_epoch

MAX_ROUTE_NAME_LENGTH = 128
MAX_CATEGORY_NAME_LENGTH = 64
RACE_STATUSES = ("draft", "upcoming", "live", "completed")


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
      Dictionary containing normalized model values plus start/end epochs.

    Notes:
      Invalid or incomplete date/time input retains the existing behavior of
      storing no epoch rather than rejecting the full race form.
    """
    def form_datetime_epoch(date_field: str, time_field: str) -> int | None:
        """Convert one complete local form date/time pair to epoch seconds."""
        date_value = (values.get(date_field) or "").strip()
        time_value = (values.get(time_field) or "").strip()
        if not date_value or not time_value:
            return None
        try:
            local_start = datetime.strptime(
                f"{date_value} {time_value}",
                "%Y-%m-%d %H:%M",
            )
            return datetime_to_epoch(local_start)
        except (TypeError, ValueError):
            return None

    return {
        "race_id": (values.get("race_id") or "").strip() or None,
        "name": (values.get("name") or "").strip(),
        "website": (values.get("website") or "").strip() or None,
        "description": (values.get("description") or "").strip() or None,
        "location": (values.get("location") or "").strip() or None,
        "logo_image_filename": normalize_static_image_filename(
            values.get("logo_image_filename")
        ),
        "starts_at_epoch": form_datetime_epoch("start_date", "start_time"),
        "ends_at_epoch": form_datetime_epoch("end_date", "end_time"),
        "status": (values.get("status") or "draft").strip().lower(),
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
