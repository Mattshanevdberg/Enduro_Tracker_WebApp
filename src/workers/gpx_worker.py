"""
Live GeoJSON cache worker (DB-polling).

Every SLEEP_SEC:
  - Enumerates distinct device_id values in points
  - Finds the latest race_rider_id linked to that device
  - Builds fresh GeoJSON in-memory via src.utils.gpx.build_geojson_for_device
    (trimmed to race_rider start/finish times when available)
  - Upserts track_cache for that race_rider_id so the live race_day page can render updated tracks

Run:
  source .venv/bin/activate
  python -m src.workers.gpx_worker

Notes:
- Simple polling; no queue required.
- Uses an in-memory watermark per device_id to skip rebuilds when no new points arrived.
"""

#### for running in vscode (comment out when on Raspberry Pi)
import sys
import os

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
####

import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select, func
from src.db.models import SessionLocal, Point, RaceRider, TrackCache, init_db
from src.utils.gpx import build_geojson_for_device
from src.utils.time import datetime_to_epoch

SLEEP_SEC = 5.0  # poll frequency; adjust as needed

def _distinct_devices(session) -> list[str]:
    """
    Return list of distinct device_id values in points.
    """
    rows = session.execute(select(Point.device_id).group_by(Point.device_id)).scalars().all()
    return [r for r in rows if r]


def _latest_race_rider_window(session, device_id: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Return the latest race_rider_id for a device plus its timing window.

    We use RFID epoch columns to match how track_hist trimming works elsewhere:
      - start_time_rfid_epoch -> start_epoch
      - finish_time_rfid_epoch -> finish_epoch
    """
    row = (
        session.execute(
            select(
                RaceRider.id,
                RaceRider.start_time_rfid_epoch,
                RaceRider.finish_time_rfid_epoch,
            )
            .where(RaceRider.device_id == device_id)
            .order_by(RaceRider.id.desc())
            .limit(1)
        )
        .first()
    )
    if not row:
        return None, None, None

    race_rider_id, start_epoch, finish_epoch = row
    # Either bound can be None (one-sided window), which keeps the query flexible.
    return race_rider_id, start_epoch, finish_epoch

def main():
    init_db()
    print("[gpx_worker] started (writing track_cache GeoJSON for live race_day)")

    # Simple watermark: cache last max(t_epoch) per device_id so we only rebuild
    # when new points arrive. Keeps the loop cheap while still reacting quickly.
    last_max_t = {}

    while True:
        session = SessionLocal()
        try:
            # For each device_id, see if new point data arrived since last time
            devices = _distinct_devices(session)
            for did in devices:
                # current latest t_epoch for this device_id
                tmax = session.execute(
                    select(func.max(Point.t_epoch)).where(Point.device_id == did)
                ).scalar()
                if tmax is None:
                    continue

                prev = last_max_t.get(did, -1)
                if tmax <= prev:
                    # no new data → skip build to avoid pointless writes
                    continue

                # Map this device_id to the latest race_rider assignment so the correct
                # track_cache row (one per rider entry) is updated. Also pull the
                # race_rider timing window so we can trim the track if times exist.
                race_rider_id, start_epoch, finish_epoch = _latest_race_rider_window(session, did)
                if race_rider_id is None:
                    # Device has points but is not linked to a race rider; skip for now.
                    continue

                # start_epoch/finish_epoch can be None; build_geojson_for_device handles
                # one-sided windows (only start or only finish) and no window at all.
                
                # TODO : if there is a race_rider_id in the track_hist then then the rider is finished the race
                # so skip the building of the geojson and rather use the track_hist geojson to update the track_cache
                # ALTERNATIVELY: dont have a seperate post race page and just use the race page and when the rider is finished
                # show the track from the track_hist table (this is what we currently have implemented)

                # Build GeoJSON in-memory (save=False) so we can store it directly in track_cache.
                # If we have a start/end window, we filter at the DB query level to keep it efficient.
                ok, geojson_or_err = build_geojson_for_device(
                    device_id=did,
                    session=session,
                    save=False,
                    start_epoch=start_epoch,
                    finish_epoch=finish_epoch,
                )
                if not ok:
                    print(f"[gpx_worker] {geojson_or_err}")
                    continue

                geojson_str = geojson_or_err
                now_epoch = datetime_to_epoch(datetime.now(timezone.utc))

                # Upsert: replace existing cache for this race_rider_id or create a new row.
                cache_row = session.get(TrackCache, race_rider_id)
                if cache_row:
                    cache_row.geojson = geojson_str
                    cache_row.updated_at_epoch = now_epoch
                else:
                    session.add(
                        TrackCache(
                            race_rider_id=race_rider_id,
                            geojson=geojson_str,
                            updated_at_epoch=now_epoch,
                        )
                    )

                session.commit()
                last_max_t[did] = tmax
                print(f"[gpx_worker] track_cache updated for race_rider_id={race_rider_id}")

        except Exception as e:
            session.rollback()
            print(f"[gpx_worker] unexpected error: {e}")
        finally:
            session.close()

        time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    main()
