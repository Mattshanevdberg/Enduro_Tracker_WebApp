"""
Pure validation helpers for developer-managed static media filenames.

Functions
---------
normalize_static_image_filename
    Trim an optional filename used beneath a known static image directory.
validate_static_image_filename
    Reject paths and unsupported image extensions while allowing a blank value.

The application stores only a filename, never an arbitrary filesystem path.
Templates choose the fixed brand/race/rider directory, preventing dashboard form
values from selecting files outside the intended static asset collection.
"""

from pathlib import PurePath


ALLOWED_STATIC_IMAGE_EXTENSIONS = {".avif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
MAX_STATIC_IMAGE_FILENAME_LENGTH = 255


def normalize_static_image_filename(value) -> str | None:
    """
    Normalize an optional developer-managed static image filename.

    Input Args:
      value: raw filename submitted by an administration form.

    Output:
      Trimmed filename or None when the value is blank or the legacy/default
      text value "None".

    Notes:
      Older race records and form rendering could expose Python's None value as
      the literal text "None". Treating that sentinel as empty prevents an
      unchanged optional image field from blocking otherwise valid race edits.
    """
    normalized = (value or "").strip()
    if not normalized or normalized.casefold() == "none":
        return None
    return normalized


def validate_static_image_filename(value: str | None) -> str | None:
    """
    Validate a filename that will be resolved beneath a fixed static directory.

    Input Args:
      value: normalized optional filename.

    Output:
      User-facing error string, or None when the value is valid.

    Notes:
      Directory separators and dot-path components are rejected. This keeps the
      database value portable and prevents it from acting as an arbitrary path.
    """
    if value is None:
        return None
    if len(value) > MAX_STATIC_IMAGE_FILENAME_LENGTH:
        return f"Image filename must be {MAX_STATIC_IMAGE_FILENAME_LENGTH} characters or fewer."
    if PurePath(value).name != value or "/" in value or "\\" in value:
        return "Image filename must not include a directory path."
    if value in {".", ".."}:
        return "Image filename is invalid."
    if PurePath(value).suffix.lower() not in ALLOWED_STATIC_IMAGE_EXTENSIONS:
        return "Image filename must use AVIF, JPEG, JPG, PNG, SVG, or WebP."
    return None
