"""
Race route/category management and GPX persistence services.

Functions
---------
create_race_route
    Validate and stage one named route owned by a race.
create_race_category
    Validate and stage one category attached to an existing race route.
create_race_category_with_route
    Attach a category to an existing route or create a named route first.
list_race_routes
    Return the named routes currently owned by a race.
list_race_categories
    Return category names currently attached to a race.
get_category_for_race
    Load one Category scoped to a race and category name.
get_route_for_category
    Load one Route scoped to a race and category name.
get_route_geojson
    Return stored route GeoJSON for a race category.
store_route_gpx
    Validate GPX and stage its source/GeoJSON on the category route.
clear_route_gpx
    Clear stored GPX/GeoJSON without deleting the Route row.
"""

from sqlalchemy import func

from src.db.models import Category, Race, Route
from src.utils.gpx import gpx_to_geojson
from src.utils.races import (
    normalize_category_name,
    normalize_route_name,
    validate_category_name,
    validate_route_name,
)


class RaceRouteValidationError(ValueError):
    """Report user-correctable route category or GPX input errors."""


class RaceRouteNotFoundError(LookupError):
    """Report that a requested race/category route does not exist."""


def create_race_route(
    session,
    race_id: int,
    route_name: str,
) -> Route:
    """
    Validate and stage one named route owned by a race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      route_name: submitted descriptive course label.

    Output:
      Newly staged Route row. The caller must commit.

    Raises:
      RaceRouteNotFoundError when the race does not exist.
      RaceRouteValidationError for a missing, long, or duplicate route name.
    """
    if session.get(Race, race_id) is None:
        raise RaceRouteNotFoundError("Race not found.")

    normalized_name = normalize_route_name(route_name)
    validation_error = validate_route_name(normalized_name)
    if validation_error:
        raise RaceRouteValidationError(validation_error)
    duplicate = session.query(Route.id).filter(
        Route.race_id == race_id,
        func.lower(Route.name) == normalized_name.lower(),
    ).first()
    if duplicate is not None:
        raise RaceRouteValidationError(
            "A route with that name already exists for this race."
        )

    route = Route(
        race_id=race_id,
        name=normalized_name,
        geojson=None,
        gpx=None,
    )
    session.add(route)
    session.flush()
    return route


def create_race_category(
    session,
    race_id: int,
    route_id: int,
    category_name: str,
) -> Category:
    """
    Validate and stage one category attached to an existing race route.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      route_id: selected Route primary key.
      category_name: submitted race category label.

    Output:
      Newly staged Category row. The caller must commit.

    Raises:
      RaceRouteValidationError for invalid input, a duplicate category, or a
      Route that is not owned by the selected Race.
    """
    normalized_name = normalize_category_name(category_name)
    validation_error = validate_category_name(normalized_name)
    if validation_error:
        raise RaceRouteValidationError(validation_error)

    route = session.query(Route).filter(
        Route.id == route_id,
        Route.race_id == race_id,
    ).one_or_none()
    if route is None:
        raise RaceRouteValidationError(
            "Selected route does not belong to this race."
        )
    duplicate = session.query(Category.id).filter(
        Category.race_id == race_id,
        func.lower(Category.name) == normalized_name.lower(),
    ).first()
    if duplicate is not None:
        raise RaceRouteValidationError(
            "A category with that name already exists for this race."
        )

    # Persist the explicit race scope alongside the route link. The composite
    # foreign key provides a second database-level same-race guarantee.
    category = Category(
        route_id=route_id,
        race_id=race_id,
        name=normalized_name,
    )
    session.add(category)
    session.flush()
    return category


def create_race_category_with_route(
    session,
    race_id: int,
    category_name: str,
    route_id: int | None = None,
    new_route_name: str | None = None,
) -> tuple[Route, Category]:
    """
    Attach a category to an existing route or create a named route first.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: submitted race category label.
      route_id: optional existing Route primary key.
      new_route_name: route name used only when route_id is None.

    Output:
      Route and newly staged Category rows. The caller must commit.

    Raises:
      RaceRouteValidationError when the selection or names are invalid.
      RaceRouteNotFoundError when a new route targets a missing race.
    """
    if route_id is None:
        route = create_race_route(session, race_id, new_route_name or "")
    else:
        route = session.query(Route).filter(
            Route.id == route_id,
            Route.race_id == race_id,
        ).one_or_none()
        if route is None:
            raise RaceRouteValidationError(
                "Selected route does not belong to this race."
            )
    category = create_race_category(
        session,
        race_id,
        route.id,
        category_name,
    )
    return route, category


def list_race_routes(session, race_id: int) -> list[Route]:
    """
    Return named routes owned by a race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.

    Output:
      Case-insensitively name-ordered Route list.
    """
    return (
        session.query(Route)
        .filter(Route.race_id == race_id)
        .order_by(func.lower(Route.name).asc(), Route.id.asc())
        .all()
    )


def list_race_categories(session, race_id: int) -> list[str]:
    """
    Return category names attached to a race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.

    Output:
      Alphabetically ordered category-name list.
    """
    return [
        row[0]
        for row in (
            session.query(Category.name)
            .join(Route, Category.route_id == Route.id)
            .filter(Route.race_id == race_id, Category.race_id == race_id)
            .order_by(Category.name.asc())
            .all()
        )
    ]


def get_category_for_race(
    session,
    race_id: int,
    category_name: str,
) -> Category | None:
    """
    Load one Category scoped to a race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: selected category label.

    Output:
      Matching Category row, or None.
    """
    return (
        session.query(Category)
        .join(Route, Category.route_id == Route.id)
        .filter(
            Route.race_id == race_id,
            Category.race_id == race_id,
            Category.name == category_name,
        )
        .one_or_none()
    )


def get_route_for_category(
    session,
    race_id: int,
    category_name: str,
) -> Route | None:
    """
    Load one Route scoped to a race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: selected category label.

    Output:
      Matching Route row, or None.
    """
    return (
        session.query(Route)
        .join(Category, Category.route_id == Route.id)
        .filter(
            Route.race_id == race_id,
            Category.race_id == race_id,
            Category.name == category_name,
        )
        .one_or_none()
    )


def get_route_geojson(session, race_id: int, category_name: str) -> str | None:
    """
    Return stored GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: selected category label.

    Output:
      GeoJSON string, or None when the route/value does not exist.
    """
    route = get_route_for_category(session, race_id, category_name)
    return route.geojson if route is not None else None


def store_route_gpx(
    session,
    race_id: int,
    category_name: str,
    gpx_text: str,
) -> tuple[Route, Category]:
    """
    Validate and stage GPX/GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: submitted category label.
      gpx_text: decoded GPX source text.

    Output:
      Updated Route/Category pair. The caller must commit.

    Raises:
      RaceRouteValidationError for an invalid category or GPX document.
    """
    category = get_category_for_race(session, race_id, category_name)
    if category is None:
        raise RaceRouteValidationError("Invalid category.")
    ok, geojson_or_error = gpx_to_geojson(gpx_text)
    if not ok:
        raise RaceRouteValidationError(geojson_or_error)

    route = category.route
    route.gpx = gpx_text
    route.geojson = geojson_or_error
    return route, category


def clear_route_gpx(session, race_id: int, category_name: str) -> Route:
    """
    Clear stored GPX/GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: selected category label.

    Output:
      Updated Route row. The caller must commit.

    Raises:
      RaceRouteNotFoundError when no route exists for the category.
    """
    route = get_route_for_category(session, race_id, category_name)
    if route is None:
        raise RaceRouteNotFoundError("No route for that category.")
    route.gpx = None
    route.geojson = None
    return route
