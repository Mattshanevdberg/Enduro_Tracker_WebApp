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
get_race_category_by_id
    Load one Category by id with explicit race scope.
rename_race_route
    Rename one route while preserving per-race uniqueness.
rename_race_category
    Rename one category and its normalized identity.
reorder_race_category
    Set one category's positive display order.
set_race_category_archived
    Archive or restore one category without deleting history.
assign_race_category_route
    Reassign one category to another route in the same race.
list_race_routes
    Return the named routes currently owned by a race.
list_race_category_records
    Return active and optionally archived Category records in display order.
get_category_for_race
    Load one Category scoped to a race and category id.
get_route_for_category
    Load one Route scoped to a race and category id.
get_route_geojson
    Return stored route GeoJSON for a race category.
store_route_gpx
    Validate GPX and stage its source/GeoJSON on the category route.
clear_route_gpx
    Clear stored GPX/GeoJSON without deleting the Route row.
category_is_unused
    Check every durable category consumer before hard deletion.
delete_unused_race_category
    Hard-delete a Category only when no historical/current data references it.
delete_unused_race_route
    Hard-delete a Route only when no Category references it.
"""

from sqlalchemy import func

from src.db.models import (
    Category,
    LeaderboardCache,
    LeaderboardHist,
    Race,
    RaceRider,
    Route,
)
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


def rename_race_route(
    session,
    race_id: int,
    route_id: int,
    route_name: str,
) -> Route:
    """
    Rename one route while preserving case-insensitive per-race uniqueness.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      route_id: Route primary key.
      route_name: submitted replacement route name.

    Output:
      Updated Route row. The caller must commit.

    Raises:
      RaceRouteValidationError for invalid input, a duplicate name, or a route
      that does not belong to the selected race.
    """
    route = session.query(Route).filter(
        Route.id == route_id,
        Route.race_id == race_id,
    ).one_or_none()
    if route is None:
        raise RaceRouteValidationError("Route does not belong to this race.")

    normalized_name = normalize_route_name(route_name)
    validation_error = validate_route_name(normalized_name)
    if validation_error:
        raise RaceRouteValidationError(validation_error)
    duplicate = session.query(Route.id).filter(
        Route.race_id == race_id,
        Route.id != route_id,
        func.lower(Route.name) == normalized_name.lower(),
    ).first()
    if duplicate is not None:
        raise RaceRouteValidationError(
            "A route with that name already exists for this race."
        )
    route.name = normalized_name
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
        Category.name_normalized == normalized_name.lower(),
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
        name_normalized=normalized_name.lower(),
        display_order=(
            session.query(func.max(Category.display_order))
            .filter(Category.race_id == race_id)
            .scalar()
            or 0
        )
        + 1,
        archived=False,
    )
    session.add(category)
    session.flush()
    return category


def get_race_category_by_id(
    session,
    race_id: int,
    category_id: int,
) -> Category | None:
    """
    Load one Category by id with explicit race scope.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: Category primary key.

    Output:
      Matching Category row, or None.
    """
    return session.query(Category).filter(
        Category.id == category_id,
        Category.race_id == race_id,
    ).one_or_none()


def rename_race_category(
    session,
    race_id: int,
    category_id: int,
    category_name: str,
) -> Category:
    """
    Rename one category and update its normalized identity.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: Category primary key.
      category_name: submitted replacement label.

    Output:
      Updated Category row. The caller must commit.

    Raises:
      RaceRouteValidationError for invalid input, duplicate identity, or a
      Category outside the selected Race.
    """
    category = get_race_category_by_id(session, race_id, category_id)
    if category is None:
        raise RaceRouteValidationError("Category does not belong to this race.")
    normalized_name = normalize_category_name(category_name)
    validation_error = validate_category_name(normalized_name)
    if validation_error:
        raise RaceRouteValidationError(validation_error)
    identity = normalized_name.lower()
    duplicate = session.query(Category.id).filter(
        Category.race_id == race_id,
        Category.id != category_id,
        Category.name_normalized == identity,
    ).first()
    if duplicate is not None:
        raise RaceRouteValidationError(
            "A category with that name already exists for this race."
        )
    category.name = normalized_name
    category.name_normalized = identity
    return category


def reorder_race_category(
    session,
    race_id: int,
    category_id: int,
    display_order: int,
) -> Category:
    """
    Set one category's positive organiser-defined display order.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: Category primary key.
      display_order: positive whole-number position.

    Output:
      Updated Category row. The caller must commit.
    """
    category = get_race_category_by_id(session, race_id, category_id)
    if category is None:
        raise RaceRouteValidationError("Category does not belong to this race.")
    if not isinstance(display_order, int) or display_order < 1:
        raise RaceRouteValidationError("Category order must be a positive number.")
    # Treat the submitted value as a position rather than an arbitrary number.
    # Re-numbering the race's full category list avoids ambiguous ties and gives
    # organisers predictable up/down movement even after categories are archived.
    ordered_categories = list_race_category_records(
        session,
        race_id,
        include_archived=True,
    )
    ordered_categories = [
        row for row in ordered_categories if row.id != category.id
    ]
    target_index = min(display_order - 1, len(ordered_categories))
    ordered_categories.insert(target_index, category)
    for position, row in enumerate(ordered_categories, start=1):
        row.display_order = position
    return category


def set_race_category_archived(
    session,
    race_id: int,
    category_id: int,
    archived: bool,
) -> Category:
    """
    Archive or restore one category without deleting historical entries.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: Category primary key.
      archived: requested archive state.

    Output:
      Updated Category row. The caller must commit.
    """
    category = get_race_category_by_id(session, race_id, category_id)
    if category is None:
        raise RaceRouteValidationError("Category does not belong to this race.")
    category.archived = bool(archived)
    return category


def assign_race_category_route(
    session,
    race_id: int,
    category_id: int,
    route_id: int,
) -> Category:
    """
    Reassign one category to another route in the same race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: Category primary key.
      route_id: replacement Route primary key.

    Output:
      Updated Category row. The caller must commit.
    """
    category = get_race_category_by_id(session, race_id, category_id)
    route = session.query(Route).filter(
        Route.id == route_id,
        Route.race_id == race_id,
    ).one_or_none()
    if category is None:
        raise RaceRouteValidationError("Category does not belong to this race.")
    if route is None:
        raise RaceRouteValidationError(
            "Selected route does not belong to this race."
        )
    category.route_id = route.id
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


def list_race_category_records(
    session,
    race_id: int,
    include_archived: bool = False,
) -> list[Category]:
    """
    Return race categories in organiser-defined display order.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      include_archived: whether retired categories should be included.

    Output:
      Ordered Category list.
    """
    query = session.query(Category).filter(Category.race_id == race_id)
    if not include_archived:
        query = query.filter(Category.archived.is_(False))
    return query.order_by(
        Category.display_order.asc(),
        func.lower(Category.name).asc(),
        Category.id.asc(),
    ).all()


def get_category_for_race(
    session,
    race_id: int,
    category_id: int,
    include_archived: bool = False,
) -> Category | None:
    """
    Load one Category scoped to a race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: selected Category primary key.
      include_archived: whether an archived category may match.

    Output:
      Matching Category row, or None.
    """
    query = session.query(Category).filter(
        Category.id == category_id,
        Category.race_id == race_id,
    )
    if not include_archived:
        query = query.filter(Category.archived.is_(False))
    return query.one_or_none()


def get_route_for_category(
    session,
    race_id: int,
    category_id: int,
) -> Route | None:
    """
    Load one Route scoped to a race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: selected Category primary key.

    Output:
      Matching Route row, or None.
    """
    return (
        session.query(Route)
        .join(Category, Category.route_id == Route.id)
        .filter(
            Route.race_id == race_id,
            Category.race_id == race_id,
            Category.id == category_id,
            Category.archived.is_(False),
        )
        .one_or_none()
    )


def get_route_geojson(session, race_id: int, category_id: int) -> str | None:
    """
    Return stored GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: selected Category primary key.

    Output:
      GeoJSON string, or None when the route/value does not exist.
    """
    route = get_route_for_category(session, race_id, category_id)
    return route.geojson if route is not None else None


def store_route_gpx(
    session,
    race_id: int,
    category_id: int,
    gpx_text: str,
) -> tuple[Route, Category]:
    """
    Validate and stage GPX/GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: submitted Category primary key.
      gpx_text: decoded GPX source text.

    Output:
      Updated Route/Category pair. The caller must commit.

    Raises:
      RaceRouteValidationError for an invalid category or GPX document.
    """
    category = get_category_for_race(session, race_id, category_id)
    if category is None:
        raise RaceRouteValidationError("Invalid category.")
    ok, geojson_or_error = gpx_to_geojson(gpx_text)
    if not ok:
        raise RaceRouteValidationError(geojson_or_error)

    route = category.route
    route.gpx = gpx_text
    route.geojson = geojson_or_error
    return route, category


def clear_route_gpx(session, race_id: int, category_id: int) -> Route:
    """
    Clear stored GPX/GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: selected Category primary key.

    Output:
      Updated Route row. The caller must commit.

    Raises:
      RaceRouteNotFoundError when no route exists for the category.
    """
    route = get_route_for_category(session, race_id, category_id)
    if route is None:
        raise RaceRouteNotFoundError("No route for that category.")
    route.gpx = None
    route.geojson = None
    return route


def category_is_unused(session, category_id: int) -> bool:
    """
    Return whether a Category has no current or historical durable consumers.

    Input Args:
      session: active SQLAlchemy session.
      category_id: Category primary key.

    Output:
      True only when RaceRider, leaderboard cache, and leaderboard history have
      no rows referencing the category.
    """
    return not any(
        session.query(model).filter(model.category_id == category_id).first()
        for model in (RaceRider, LeaderboardCache, LeaderboardHist)
    )


def delete_unused_race_category(
    session,
    race_id: int,
    category_id: int,
) -> Category:
    """
    Hard-delete one category only when it has never acquired durable history.

    Referenced categories must be archived instead so RaceRider, leaderboard,
    timing, and result history retain the same stable Category id and label.
    """
    category = get_category_for_race(
        session,
        race_id,
        category_id,
        include_archived=True,
    )
    if category is None:
        raise RaceRouteNotFoundError("Category not found for this race.")
    if not category_is_unused(session, category.id):
        raise RaceRouteValidationError(
            "Referenced categories cannot be deleted. Archive this category instead."
        )
    session.delete(category)
    return category


def delete_unused_race_route(
    session,
    race_id: int,
    route_id: int,
) -> Route:
    """
    Hard-delete one route only when no active or archived category uses it.

    Input Args:
      session: active SQLAlchemy session.
      race_id: owning Race primary key.
      route_id: Route primary key.

    Output:
      Deleted Route row pending caller commit.
    """
    route = session.query(Route).filter(
        Route.id == route_id,
        Route.race_id == race_id,
    ).one_or_none()
    if route is None:
        raise RaceRouteNotFoundError("Route not found for this race.")
    if session.query(Category.id).filter(Category.route_id == route.id).first():
        raise RaceRouteValidationError(
            "Routes used by a category cannot be deleted. Reassign or remove the unused category first."
        )
    session.delete(route)
    return route
