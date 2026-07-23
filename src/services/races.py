"""
Race lifecycle and page-level application services.

Functions
---------
get_race
    Load one Race by primary key.
save_race
    Validate and stage race creation or editing.
save_race_with_image_feedback
    Save valid race fields while preserving the prior image after an invalid
    optional image filename submission.
load_post_race_data
    Build race/category/route/rider data for the post-race page.
load_race_edit_data
    Build route and assignment management data for the race edit page.

Specialized route, rider-entry, and timing services are composed here so the
Flask controller can remain focused on HTTP parsing and responses.
"""

from dataclasses import dataclass

from src.db.models import Race
from src.services.race_riders import load_race_rider_management_data
from src.services.race_routes import (
    get_category_for_race,
    get_route_geojson,
    list_race_category_records,
    list_race_routes,
)
from src.services.race_timing import build_post_race_riders
from src.utils.media import validate_static_image_filename
from src.utils.races import RACE_STATUSES
from src.utils.time import epoch_to_datetime


class RaceValidationError(ValueError):
    """Report user-correctable race form errors."""


class RaceNotFoundError(LookupError):
    """Report that a requested Race row does not exist."""


@dataclass(frozen=True)
class RaceSaveResult:
    """
    Describe a staged race save and any non-blocking image-field error.

    Attributes:
      race: new or updated Race row staged in the supplied session.
      image_error: optional validation message when the submitted image was
        rejected and the previous image value was retained.
      submitted_image_filename: rejected filename retained for form feedback.
    """

    race: Race
    image_error: str | None = None
    submitted_image_filename: str | None = None


def get_race(session, race_id: int) -> Race | None:
    """
    Load one Race by primary key.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.

    Output:
      Matching Race row, or None.
    """
    return session.get(Race, race_id)


def _prepare_race_display_times(race: Race) -> Race:
    """
    Attach dashboard/form-compatible race start and end datetimes.

    Input Args:
      race: Race row using start/end epochs as durable time storage.

    Output:
      Same Race row with starts_at and ends_at display attributes populated.
    """
    race.starts_at = (
        epoch_to_datetime(race.starts_at_epoch)
        if race.starts_at_epoch is not None
        else None
    )
    race.ends_at = (
        epoch_to_datetime(race.ends_at_epoch)
        if race.ends_at_epoch is not None
        else None
    )
    return race


def save_race(session, form: dict) -> Race:
    """
    Validate and stage race creation or editing.

    Input Args:
      session: active SQLAlchemy session.
      form: normalized values from src.utils.races.normalize_race_form.

    Output:
      New or updated Race row. The caller must commit.

    Raises:
      RaceValidationError when the name or race id is invalid.
      RaceNotFoundError when an edit target does not exist.
    """
    if not form.get("name"):
        raise RaceValidationError("Race name is required.")
    if form.get("status") not in RACE_STATUSES:
        raise RaceValidationError("Select a valid race status.")
    if (
        form.get("starts_at_epoch") is not None
        and form.get("ends_at_epoch") is not None
        and form["ends_at_epoch"] < form["starts_at_epoch"]
    ):
        raise RaceValidationError("Race end time cannot be before its start time.")
    image_error = validate_static_image_filename(form.get("logo_image_filename"))
    if image_error:
        raise RaceValidationError(image_error)

    race_id = form.get("race_id")
    if race_id is not None:
        try:
            race_id = int(race_id)
        except (TypeError, ValueError) as error:
            raise RaceValidationError("Race id is invalid.") from error
        race = get_race(session, race_id)
        if race is None:
            raise RaceNotFoundError("Race not found.")
    else:
        race = Race()
        session.add(race)

    race.name = form["name"]
    race.website = form.get("website")
    race.description = form.get("description")
    race.location = form.get("location")
    race.logo_image_filename = form.get("logo_image_filename")
    race.starts_at_epoch = form.get("starts_at_epoch")
    race.ends_at_epoch = form.get("ends_at_epoch")
    race.status = form["status"]
    session.flush()
    return race


def save_race_with_image_feedback(session, form: dict) -> RaceSaveResult:
    """
    Save a race while treating an invalid optional image as a field warning.

    Input Args:
      session: active SQLAlchemy session.
      form: normalized values from src.utils.races.normalize_race_form.

    Output:
      RaceSaveResult containing the staged Race and optional image warning.
      The caller must commit.

    Raises:
      RaceValidationError for blocking race-field validation errors.
      RaceNotFoundError when an edit target does not exist.

    Notes:
      Race metadata such as name, dates, location, description, and lifecycle
      status should not be lost because an optional developer-managed image
      filename is malformed. When that one field is invalid, this function
      retains the existing image for edits (or None for a new race), saves the
      remaining valid fields, and returns the image error for inline display.
    """
    submitted_image_filename = form.get("logo_image_filename")
    image_error = validate_static_image_filename(submitted_image_filename)
    if image_error is None:
        return RaceSaveResult(race=save_race(session, form))

    fallback_image_filename = None
    race_id = form.get("race_id")
    if race_id is not None:
        try:
            parsed_race_id = int(race_id)
        except (TypeError, ValueError) as error:
            raise RaceValidationError("Race id is invalid.") from error
        existing_race = get_race(session, parsed_race_id)
        if existing_race is None:
            raise RaceNotFoundError("Race not found.")
        fallback_image_filename = existing_race.logo_image_filename

    safe_form = dict(form)
    safe_form["logo_image_filename"] = fallback_image_filename
    race = save_race(session, safe_form)
    return RaceSaveResult(
        race=race,
        image_error=image_error,
        submitted_image_filename=submitted_image_filename,
    )


def load_post_race_data(
    session,
    race_id: int,
    requested_category_id: int | None,
) -> dict:
    """
    Build all durable/display data required by the post-race page.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      requested_category_id: optional Category primary key query parameter.

    Output:
      Dictionary containing race, categories, selection, route GeoJSON, rider
      timing rows, and the aggregate RFID warning flag.

    Raises:
      RaceNotFoundError when the race does not exist.
    """
    race = get_race(session, race_id)
    if race is None:
        raise RaceNotFoundError("Race not found.")
    _prepare_race_display_times(race)

    categories = list_race_category_records(session, race_id)
    selected_category = (
        get_category_for_race(session, race_id, requested_category_id)
        if requested_category_id is not None
        else None
    )
    if requested_category_id is not None and selected_category is None:
        raise RaceValidationError("Category not found for this race.")
    if selected_category is None and categories:
        selected_category = categories[0]
    geojson = (
        get_route_geojson(session, race_id, selected_category.id)
        if selected_category is not None
        else None
    )
    riders = (
        build_post_race_riders(session, selected_category.id)
        if selected_category is not None
        else []
    )
    return {
        "race": race,
        "categories": categories,
        "selected_category": selected_category,
        "geojson": geojson,
        "riders": riders,
        "has_multiple_rfid_flag": any(
            rider.get("multiple_rfid_flag") for rider in riders
        ),
    }


def load_race_edit_data(
    session,
    race_id: int,
    requested_category_id: int | None,
) -> dict:
    """
    Build all durable/display data required by the race edit page.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      requested_category_id: optional selected Category primary key.

    Output:
      Dictionary containing race routes/categories, the selected Route/Category,
      GeoJSON, and reusable rider/device assignment management data.

    Raises:
      RaceNotFoundError when the race does not exist.
    """
    race = get_race(session, race_id)
    if race is None:
        raise RaceNotFoundError("Race not found.")
    _prepare_race_display_times(race)
    categories = list_race_category_records(session, race_id)
    selected_category = (
        get_category_for_race(session, race_id, requested_category_id)
        if requested_category_id is not None
        else None
    )
    if requested_category_id is not None and selected_category is None:
        raise RaceValidationError("Category not found for this race.")
    if selected_category is None and categories:
        selected_category = categories[0]
    route = selected_category.route if selected_category is not None else None
    # A newly created race has neither a route nor category. Keep the controller
    # and template read-only on GET by returning an explicit empty management
    # payload until the organiser creates a category.
    management = (
        load_race_rider_management_data(session, selected_category.id)
        if selected_category is not None
        else {
            "riders": [],
            "devices": [],
            "race_riders": [],
            "last_device_by_rider": {},
        }
    )
    return {
        "race": race,
        "routes": list_race_routes(session, race_id),
        "category_records": list_race_category_records(
            session,
            race_id,
            include_archived=True,
        ),
        "categories": categories,
        "selected_category": selected_category,
        "route": route,
        "geojson": route.geojson if route is not None else None,
        **management,
    }
