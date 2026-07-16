"""
Pure RFID viewer filter normalization and parsing helpers.

Functions
---------
normalize_rfid_filters
    Build stable, trimmed filter values for the RFID viewer.
parse_optional_int
    Parse an optional whole-number filter.
parse_rfid_limit
    Parse and clamp the requested row limit.
datetime_filter_to_epoch
    Convert an optional datetime-local filter to epoch seconds.

The helpers avoid Flask, SQLAlchemy, and templates. Datetime parsing delegates
to the existing shared time utility so timezone behavior remains consistent.
"""

from src.utils.time import iso_to_epoch

DEFAULT_RFID_LIMIT = 200
MAX_RFID_LIMIT = 1000
RFID_FILTER_NAMES = (
    "id",
    "epc",
    "reader_id",
    "ant",
    "time_from",
    "time_to",
    "received_from",
    "received_to",
)


def normalize_rfid_filters(values) -> dict:
    """
    Normalize raw RFID viewer filter values.

    Input Args:
      values: mapping-like object containing query/filter values.

    Output:
      Dictionary containing every supported filter as a trimmed string.
    """
    filters = {
        name: (values.get(name) or "").strip()
        for name in RFID_FILTER_NAMES
    }
    filters["limit"] = (values.get("limit") or str(DEFAULT_RFID_LIMIT)).strip()
    return filters


def parse_optional_int(value) -> int | None:
    """
    Parse an optional whole-number value.

    Input Args:
      value: raw optional string or integer value.

    Output:
      Parsed integer, or None when the input is empty.

    Raises:
      ValueError when a non-empty value cannot be converted to an integer.
    """
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return int(normalized)


def parse_rfid_limit(value) -> int:
    """
    Parse and clamp the RFID viewer row limit.

    Input Args:
      value: raw optional row-limit value.

    Output:
      Integer between 1 and MAX_RFID_LIMIT, defaulting to DEFAULT_RFID_LIMIT.
    """
    parsed = parse_optional_int(value)
    if parsed is None:
        return DEFAULT_RFID_LIMIT
    return max(1, min(parsed, MAX_RFID_LIMIT))


def datetime_filter_to_epoch(value) -> int | None:
    """
    Convert an optional datetime-local filter to epoch seconds.

    Input Args:
      value: raw datetime-local string.

    Output:
      Epoch seconds, or None when the input is empty.

    Raises:
      ValueError when the non-empty value is not a supported ISO datetime.
    """
    if value is None or not str(value).strip():
        return None
    return iso_to_epoch(str(value).strip())
