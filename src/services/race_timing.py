"""
Race-rider RFID timing, payload, and track-snapshot services.

Functions
---------
race_rider_timing_payload
    Format one RaceRider timing state for templates/JSON.
build_post_race_riders
    Build rider identity/device/timing rows for the post-race page.
list_race_rider_timings
    Return timing payloads scoped to a race and optional category.
update_manual_race_rider_times
    Stage manual timing changes and an optional trimmed track snapshot.
confirm_race_rider_finish
    Confirm the current finish time and clear its warning flag.
"""

from src.db.models import RaceRider, Rider, TrackHist
from src.services.race_riders import get_scoped_race_rider
from src.utils.gpx import build_track_snapshot_from_raw_text
from src.utils.time import datetime_to_epoch, epoch_to_datetime, utc_now


class RaceRiderTimingNotFoundError(LookupError):
    """Report that a timing operation could not find the scoped race entry."""


class RaceRiderFinishMissingError(ValueError):
    """Report that a finish cannot be confirmed before a finish time exists."""


def race_rider_timing_payload(race_rider: RaceRider) -> dict:
    """
    Build template/JSON timing values for one race entry.

    Input Args:
      race_rider: RaceRider row with RFID epoch and warning fields.

    Output:
      Dictionary containing display strings, datetime-local values, the visible
      multiple-read warning, and finish-confirmation state.
    """
    start_dt = (
        epoch_to_datetime(race_rider.start_time_rfid_epoch).replace(tzinfo=None)
        if race_rider.start_time_rfid_epoch is not None
        else None
    )
    finish_dt = (
        epoch_to_datetime(race_rider.finish_time_rfid_epoch).replace(tzinfo=None)
        if race_rider.finish_time_rfid_epoch is not None
        else None
    )
    finish_confirmed = bool(race_rider.finish_time_rfid_confirmed)
    show_multiple_warning = bool(race_rider.multiple_rfid_flag) and not finish_confirmed
    return {
        "race_rider_id": race_rider.id,
        "start_time_rfid": (
            start_dt.strftime("%Y-%m-%d %H:%M:%S") if start_dt else None
        ),
        "finish_time_rfid": (
            finish_dt.strftime("%Y-%m-%d %H:%M:%S") if finish_dt else None
        ),
        "start_time_input": (
            start_dt.strftime("%Y-%m-%dT%H:%M:%S") if start_dt else ""
        ),
        "finish_time_input": (
            finish_dt.strftime("%Y-%m-%dT%H:%M:%S") if finish_dt else ""
        ),
        "multiple_rfid_flag": show_multiple_warning,
        "finish_time_rfid_confirmed": finish_confirmed,
    }


def build_post_race_riders(session, category_id: int) -> list[dict]:
    """
    Build post-race rider identity, device, and timing rows.

    Input Args:
      session: active SQLAlchemy session.
      category_id: selected Category primary key.

    Output:
      Rider dictionaries ordered by name for the post-race template.
    """
    rows = (
        session.query(Rider.name, Rider.team, RaceRider)
        .join(RaceRider, RaceRider.rider_id == Rider.id)
        .filter(RaceRider.category_id == category_id)
        .order_by(Rider.name.asc())
        .all()
    )
    return [
        {
            "name": name,
            "team": team,
            "device_id": race_rider.device_id,
            "race_rider_id": race_rider.id,
            **race_rider_timing_payload(race_rider),
        }
        for name, team, race_rider in rows
    ]


def list_race_rider_timings(
    session,
    race_id: int,
    category_id: int | None = None,
) -> list[dict]:
    """
    Return timing payloads scoped to a race and optional category.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      category_id: optional selected Category primary key.

    Output:
      RaceRider timing dictionaries ordered by entry id.
    """
    query = (
        session.query(RaceRider)
        .filter(RaceRider.race_id == race_id)
        .order_by(RaceRider.id.asc())
    )
    if category_id is not None:
        query = query.filter(RaceRider.category_id == category_id)
    return [race_rider_timing_payload(row) for row in query.all()]


def update_manual_race_rider_times(
    session,
    race_id: int,
    race_rider_id: int,
    start_epoch: int | None,
    finish_epoch: int | None,
) -> RaceRider:
    """
    Stage manual timing changes and an optional trimmed track snapshot.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.
      start_epoch: replacement start time or None to clear it.
      finish_epoch: replacement finish time or None to clear it.

    Output:
      Updated RaceRider row. The caller must commit.

    Raises:
      RaceRiderTimingNotFoundError when the entry is not in the requested race.
    """
    race_rider = get_scoped_race_rider(session, race_id, race_rider_id)
    if race_rider is None:
        raise RaceRiderTimingNotFoundError("Race rider not found")

    race_rider.start_time_rfid_epoch = start_epoch
    race_rider.finish_time_rfid_epoch = finish_epoch
    race_rider.finish_time_rfid_confirmed = finish_epoch is not None
    if race_rider.finish_time_rfid_confirmed:
        race_rider.multiple_rfid_flag = False

    latest_track = (
        session.query(TrackHist)
        .filter(TrackHist.race_rider_id == race_rider.id)
        .order_by(TrackHist.id.desc())
        .first()
    )
    if latest_track and latest_track.raw_txt:
        snapshot = build_track_snapshot_from_raw_text(
            latest_track.raw_txt,
            start_epoch=start_epoch,
            finish_epoch=finish_epoch,
            creator=f"EnduroTracker {race_rider.device_id}",
        )
        if snapshot is not None:
            gpx_text, geojson_text = snapshot
            session.add(
                TrackHist(
                    race_rider_id=race_rider.id,
                    geojson=geojson_text,
                    gpx=gpx_text,
                    raw_txt=latest_track.raw_txt,
                    updated_at_epoch=datetime_to_epoch(utc_now()),
                )
            )
    return race_rider


def confirm_race_rider_finish(
    session,
    race_id: int,
    race_rider_id: int,
) -> RaceRider:
    """
    Confirm a race entry's current RFID finish time.

    Input Args:
      session: active SQLAlchemy session.
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      Updated RaceRider row. The caller must commit.

    Raises:
      RaceRiderTimingNotFoundError when the scoped entry does not exist.
      RaceRiderFinishMissingError when the entry has no finish time.
    """
    race_rider = get_scoped_race_rider(session, race_id, race_rider_id)
    if race_rider is None:
        raise RaceRiderTimingNotFoundError("Race rider not found")
    if race_rider.finish_time_rfid_epoch is None:
        raise RaceRiderFinishMissingError("Cannot confirm a missing finish time")
    race_rider.finish_time_rfid_confirmed = True
    race_rider.multiple_rfid_flag = False
    return race_rider
