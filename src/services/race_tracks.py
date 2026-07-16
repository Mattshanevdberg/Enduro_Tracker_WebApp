"""
Race-rider track history/cache retrieval services.

Functions
---------
read_track_history_geojson
    Return the newest historical GeoJSON scoped to a race entry.
read_track_cache_geojson
    Return live cached GeoJSON scoped to a race entry.
get_race_rider_track_geojson
    Apply history/cache preference and fallback behavior.
"""

from src.db.models import Category, RaceRider, Route, TrackCache, TrackHist


def read_track_history_geojson(
    session,
    race_id: int,
    race_rider_id: int,
) -> str | None:
    """
    Return the newest historical track GeoJSON scoped to a race entry.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      GeoJSON string, or None when no matching history exists.
    """
    row = (
        session.query(TrackHist.geojson)
        .join(RaceRider, TrackHist.race_rider_id == RaceRider.id)
        .join(Category, Category.id == RaceRider.category_id)
        .join(Route, Route.id == Category.route_id)
        .filter(Route.race_id == race_id, RaceRider.id == race_rider_id)
        .order_by(TrackHist.id.desc())
        .first()
    )
    return row[0] if row and row[0] else None


def read_track_cache_geojson(
    session,
    race_id: int,
    race_rider_id: int,
) -> str | None:
    """
    Return live cached track GeoJSON scoped to a race entry.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      GeoJSON string, or None when no matching cache exists.
    """
    row = (
        session.query(TrackCache.geojson)
        .join(RaceRider, TrackCache.race_rider_id == RaceRider.id)
        .join(Category, Category.id == RaceRider.category_id)
        .join(Route, Route.id == Category.route_id)
        .filter(Route.race_id == race_id, RaceRider.id == race_rider_id)
        .first()
    )
    return row[0] if row and row[0] else None


def get_race_rider_track_geojson(
    session,
    race_id: int,
    race_rider_id: int,
    prefer_cache: bool = False,
) -> str | None:
    """
    Return race-rider track GeoJSON using configured cache/history preference.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.
      prefer_cache: when True, query the live cache before history.

    Output:
      First available GeoJSON string, or None.
    """
    if prefer_cache:
        cached = read_track_cache_geojson(session, race_id, race_rider_id)
        if cached:
            return cached
    historical = read_track_history_geojson(session, race_id, race_rider_id)
    if historical:
        return historical
    return read_track_cache_geojson(session, race_id, race_rider_id)
