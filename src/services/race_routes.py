"""
Race category/route lookup and GPX persistence services.

Functions
---------
find_or_create_route_for_category
    Return or stage the Route/Category pair for a race category.
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

from src.db.models import Category, Route
from src.utils.gpx import gpx_to_geojson


class RaceRouteValidationError(ValueError):
    """Report user-correctable route category or GPX input errors."""


class RaceRouteNotFoundError(LookupError):
    """Report that a requested race/category route does not exist."""


def find_or_create_route_for_category(
    session,
    race_id: int,
    category_name: str,
) -> tuple[Route, Category]:
    """
    Return or stage the Route/Category pair for a race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: selected category label.

    Output:
      Tuple containing the existing or newly staged Route and Category rows.
    """
    route = (
        session.query(Route)
        .join(Category, Category.route_id == Route.id)
        .filter(Route.race_id == race_id, Category.name == category_name)
        .one_or_none()
    )
    if route is not None:
        category = (
            session.query(Category)
            .filter(
                Category.route_id == route.id,
                Category.name == category_name,
            )
            .one()
        )
        return route, category

    route = Route(race_id=race_id, geojson=None, gpx=None)
    session.add(route)
    session.flush()
    category = Category(route_id=route.id, name=category_name)
    session.add(category)
    session.flush()
    return route, category


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
            .filter(Route.race_id == race_id)
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
        .filter(Route.race_id == race_id, Category.name == category_name)
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
        .filter(Route.race_id == race_id, Category.name == category_name)
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
    allowed_categories,
) -> tuple[Route, Category]:
    """
    Validate and stage GPX/GeoJSON for one race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_name: submitted category label.
      gpx_text: decoded GPX source text.
      allowed_categories: supported race categories.

    Output:
      Updated Route/Category pair. The caller must commit.

    Raises:
      RaceRouteValidationError for an invalid category or GPX document.
    """
    if category_name not in tuple(allowed_categories or ()):
        raise RaceRouteValidationError("Invalid category.")
    ok, geojson_or_error = gpx_to_geojson(gpx_text)
    if not ok:
        raise RaceRouteValidationError(geojson_or_error)

    route, category = find_or_create_route_for_category(
        session,
        race_id,
        category_name,
    )
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
