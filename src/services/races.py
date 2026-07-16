"""
Race lifecycle and page-level application services.

Functions
---------
get_race
    Load one Race by primary key.
save_race
    Validate and stage race creation or editing.
load_post_race_data
    Build race/category/route/rider data for the post-race page.
load_race_edit_data
    Build route and assignment management data for the race edit page.

Specialized route, rider-entry, and timing services are composed here so the
Flask controller can remain focused on HTTP parsing and responses.
"""

from src.db.models import Race
from src.services.race_riders import load_race_rider_management_data
from src.services.race_routes import (
    find_or_create_route_for_category,
    get_category_for_race,
    get_route_geojson,
    list_race_categories,
)
from src.services.race_timing import build_post_race_riders
from src.utils.races import DEFAULT_RACE_CATEGORIES, select_category
from src.utils.time import epoch_to_datetime


class RaceValidationError(ValueError):
    """Report user-correctable race form errors."""


class RaceNotFoundError(LookupError):
    """Report that a requested Race row does not exist."""


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


def _prepare_race_display_time(race: Race) -> Race:
    """
    Attach the dashboard/form-compatible race start datetime.

    Input Args:
      race: Race row using starts_at_epoch as durable time storage.

    Output:
      Same Race row with its starts_at display attribute populated.
    """
    race.starts_at = (
        epoch_to_datetime(race.starts_at_epoch)
        if race.starts_at_epoch is not None
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
    race.starts_at_epoch = form.get("starts_at_epoch")
    race.active = bool(form.get("active"))
    session.flush()
    return race


def load_post_race_data(
    session,
    race_id: int,
    requested_category: str | None,
) -> dict:
    """
    Build all durable/display data required by the post-race page.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      requested_category: optional category query parameter.

    Output:
      Dictionary containing race, categories, selection, route GeoJSON, rider
      timing rows, and the aggregate RFID warning flag.

    Raises:
      RaceNotFoundError when the race does not exist.
    """
    race = get_race(session, race_id)
    if race is None:
        raise RaceNotFoundError("Race not found.")
    _prepare_race_display_time(race)

    categories = list_race_categories(session, race_id)
    selected_category = select_category(requested_category, categories)
    category = (
        get_category_for_race(session, race_id, selected_category)
        if selected_category
        else None
    )
    geojson = (
        get_route_geojson(session, race_id, selected_category)
        if selected_category
        else None
    )
    riders = build_post_race_riders(session, category.id) if category else []
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
    requested_category: str | None,
    allowed_categories=DEFAULT_RACE_CATEGORIES,
) -> dict:
    """
    Build all durable/display data required by the race edit page.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      requested_category: optional selected category query parameter.
      allowed_categories: ordered supported category names.

    Output:
      Dictionary containing race, selected Route/Category, GeoJSON, and reusable
      rider/device assignment management data.

    Raises:
      RaceNotFoundError when the race does not exist.
    """
    race = get_race(session, race_id)
    if race is None:
        raise RaceNotFoundError("Race not found.")
    selected_category = select_category(requested_category, allowed_categories)
    route, category = find_or_create_route_for_category(
        session,
        race_id,
        selected_category,
    )
    management = load_race_rider_management_data(session, category.id)
    return {
        "race": race,
        "categories": list(allowed_categories),
        "selected_category": selected_category,
        "route": route,
        "geojson": route.geojson,
        **management,
    }
