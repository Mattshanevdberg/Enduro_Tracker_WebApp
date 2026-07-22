"""
Pure rider form normalization, formatting, and validation helpers.

Functions
---------
normalize_rider_form
    Trim rider text values and normalize blank optional fields.
rider_form_values
    Build template-safe values from a Rider-like object or an empty form.
validate_rider_form
    Validate the required rider text fields.

These helpers deliberately avoid Flask, SQLAlchemy, and template rendering so
the same rider input rules can be reused by browser routes and future APIs.
"""

from collections.abc import Mapping

def normalize_rider_form(
    name: str | None,
    team: str | None,
    bike: str | None,
    bio: str | None,
) -> dict:
    """
    Normalize raw rider form values.

    Input Args:
      name: raw rider full name.
      team: raw optional team name.
      bike: raw optional bike description.
      bio: raw optional rider biography.
    Output:
      Dictionary with trimmed values and None for blank optional fields.
    """
    return {
        "name": (name or "").strip(),
        "team": (team or "").strip() or None,
        "bike": (bike or "").strip() or None,
        "bio": (bio or "").strip() or None,
    }


def rider_form_values(rider=None) -> dict:
    """
    Build template-safe rider form values.

    Input Args:
      rider: optional Rider-like object or normalized dictionary whose fields
        should populate the form.

    Output:
      Dictionary containing strings for all rider form fields.
    """
    if isinstance(rider, Mapping):
        value_for = lambda field: rider.get(field) or ""
    else:
        value_for = lambda field: getattr(rider, field, "") or ""

    return {
        "name": value_for("name"),
        "team": value_for("team"),
        "bike": value_for("bike"),
        "bio": value_for("bio"),
        "profile_image_filename": value_for("profile_image_filename"),
    }


def validate_rider_form(form: dict) -> list[str]:
    """
    Validate database-independent rider form rules.

    Input Args:
      form: normalized dictionary returned by normalize_rider_form.

    Output:
      List of validation messages. An empty list means the values are valid.
    """
    errors = []
    if not form.get("name"):
        errors.append("Name is required.")
    return errors
