"""
Public/admin dashboard and sitemap data-composition services.

Functions
---------
load_race_display_data
    Load races for optional lifecycle statuses and attach display datetimes.
load_dashboard_display_data
    Compose upcoming/live/completed race collections and all public riders.
list_public_rider_ids
    Return stable Rider identifiers for public sitemap generation.

This service coordinates Race/Rider queries and dashboard display values without
depending on Flask requests, routes, template rendering, or URL construction.
"""

from src.db.models import Race, Rider
from src.services.riders import list_riders
from src.utils.time import epoch_to_datetime


PUBLIC_RACE_STATUSES = ("upcoming", "live", "completed")


def _prepare_race_display_values(race: Race) -> Race:
    """
    Attach timezone-aware start/end datetimes used by dashboard templates.

    Input Args:
      race: Race row whose durable timestamps are epoch seconds.

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


def load_race_display_data(
    session,
    statuses: tuple[str, ...] | list[str] | set[str] | None = None,
) -> list[Race]:
    """
    Load races and prepare the values required by dashboard templates.

    Input Args:
      session: active SQLAlchemy session.
      statuses: optional lifecycle statuses to include; None returns all races.

    Output:
      Race rows ordered by start epoch with display datetimes attached.
    """
    query = session.query(Race)
    if statuses is not None:
        query = query.filter(Race.status.in_(tuple(statuses)))
    races = query.order_by(Race.starts_at_epoch.asc(), Race.id.asc()).all()
    return [_prepare_race_display_values(race) for race in races]


def load_dashboard_display_data(session) -> dict:
    """
    Compose all server-rendered public dashboard collections.

    Input Args:
      session: active SQLAlchemy session.

    Output:
      Dictionary containing race lists keyed by dashboard tab plus all riders.

    Notes:
      Completed races are newest-first for useful archive browsing. Draft races
      are deliberately absent from the public dashboard.
    """
    races = load_race_display_data(session, statuses=PUBLIC_RACE_STATUSES)
    sections = {
        "upcoming": [],
        "live": [],
        "past": [],
    }
    for race in races:
        section_key = "past" if race.status == "completed" else race.status
        sections[section_key].append(race)
    sections["past"].reverse()
    return {
        "race_sections": sections,
        "riders": list_riders(session),
    }


def list_public_rider_ids(session) -> list[int]:
    """
    Return public Rider primary keys in deterministic order for the sitemap.

    Input Args:
      session: active SQLAlchemy session.

    Output:
      Rider ids ordered by primary key.
    """
    return [row.id for row in session.query(Rider.id).order_by(Rider.id.asc()).all()]
