"""
Race-rider entry assignment and management services.

Functions
---------
get_scoped_race_rider
    Load one RaceRider only when it belongs to the requested race.
load_race_rider_management_data
    Build rider/device/entry selector data for the race edit page.
create_race_rider
    Stage a new rider/device/category assignment.
update_race_rider
    Stage device and active/recording changes.
delete_race_rider
    Stage removal of an assignment.

Existing rider/device listing services are reused so ordering rules are not
duplicated across feature services.
"""

from src.db.models import RaceRider
from src.services.devices import list_devices
from src.services.riders import list_riders


def get_scoped_race_rider(
    session,
    race_id: int,
    race_rider_id: int,
) -> RaceRider | None:
    """
    Load a RaceRider only when it belongs to the requested race.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      Matching RaceRider row, or None.
    """
    return (
        session.query(RaceRider)
        .filter(
            RaceRider.id == race_rider_id,
            RaceRider.race_id == race_id,
        )
        .one_or_none()
    )


def load_race_rider_management_data(session, category_id: int) -> dict:
    """
    Build selector and assignment data for a race category edit page.

    Input Args:
      session: active SQLAlchemy session.
      category_id: selected Category primary key.

    Output:
      Dictionary containing available riders, devices, existing assignments,
      and each rider's most recently used device id.
    """
    riders = list_riders(session)
    devices = list_devices(session)
    race_riders = (
        session.query(RaceRider)
        .filter(RaceRider.category_id == category_id)
        .order_by(RaceRider.id.asc())
        .all()
    )

    # Preserve the established "highest RaceRider id is most recent" behavior.
    last_device_by_rider = {}
    for rider in riders:
        latest = (
            session.query(RaceRider)
            .filter(RaceRider.rider_id == rider.id)
            .order_by(RaceRider.id.desc())
            .first()
        )
        last_device_by_rider[rider.id] = latest.device_id if latest else None

    selected_rider_ids = {row.rider_id for row in race_riders}
    available_riders = [
        rider for rider in riders if rider.id not in selected_rider_ids
    ]
    return {
        "riders": available_riders,
        "devices": devices,
        "race_riders": race_riders,
        "last_device_by_rider": last_device_by_rider,
    }


def create_race_rider(
    session,
    race_id: int,
    rider_id: int,
    device_id: str,
    category_id: int,
) -> RaceRider:
    """
    Stage a new rider assignment for a race category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key shared by the entry and selected Category.
      rider_id: Rider primary key.
      device_id: selected Device primary key.
      category_id: Category primary key.

    Output:
      Newly staged active/recording RaceRider row. The caller must commit.
    """
    race_rider = RaceRider(
        race_id=race_id,
        rider_id=rider_id,
        device_id=device_id,
        category_id=category_id,
        active=True,
        recording=True,
    )
    session.add(race_rider)
    return race_rider


def update_race_rider(
    race_rider: RaceRider,
    device_id: str,
    active: bool,
    recording: bool,
) -> RaceRider:
    """
    Stage editable assignment changes.

    Input Args:
      race_rider: existing RaceRider row.
      device_id: selected Device primary key.
      active: whether the entry is active.
      recording: whether tracker recording is enabled.

    Output:
      Updated RaceRider row. The caller must commit.
    """
    race_rider.device_id = device_id
    race_rider.active = active
    race_rider.recording = recording
    return race_rider


def delete_race_rider(session, race_rider: RaceRider) -> None:
    """
    Stage deletion of a race-rider assignment.

    Input Args:
      session: active SQLAlchemy session.
      race_rider: existing scoped RaceRider row.

    Output:
      None. The caller must commit.
    """
    session.delete(race_rider)
