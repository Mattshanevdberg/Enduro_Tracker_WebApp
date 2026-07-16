"""
Dashboard race loading and display preparation services.

Functions
---------
load_race_display_data
    Load dashboard races, attach display datetimes, and select a default category.

This service coordinates Race queries and dashboard display values without
depending on Flask requests, routes, or template rendering. It reuses the shared
epoch conversion and rider-category defaults already used elsewhere.
"""

from src.db.models import Race
from src.utils.riders import DEFAULT_RIDER_CATEGORIES
from src.utils.time import epoch_to_datetime


def load_race_display_data(
    session,
    active_only: bool = False,
    categories=DEFAULT_RIDER_CATEGORIES,
) -> tuple[list[Race], str]:
    """
    Load races and prepare the values required by dashboard templates.

    Input Args:
      session: active SQLAlchemy session.
      active_only: when True, return only active races.
      categories: ordered iterable of configured race category names.

    Output:
      Tuple containing ordered Race rows and the default category name.

    Notes:
      starts_at_epoch remains the durable time value. The starts_at attribute is
      populated with a display datetime for the existing dashboard templates.
    """
    query = session.query(Race)
    if active_only:
        query = query.filter(Race.active.is_(True))
    races = query.order_by(Race.starts_at_epoch.asc()).all()

    # Reuse the shared timezone-aware epoch converter so dashboard times follow
    # the same application timezone behavior as race and RFID pages.
    for race in races:
        race.starts_at = (
            epoch_to_datetime(race.starts_at_epoch)
            if race.starts_at_epoch is not None
            else None
        )

    configured_categories = tuple(categories or DEFAULT_RIDER_CATEGORIES)
    default_category = configured_categories[0] if configured_categories else ""
    return races, default_category
