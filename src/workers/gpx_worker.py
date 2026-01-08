"""
Live GeoJSON cache worker (DB-polling).

Every SLEEP_SEC:
  - Enumerates distinct device_id values in points
  - Finds the latest race_rider_id linked to that device
  - Builds fresh GeoJSON in-memory via src.utils.gpx.build_geojson_for_device
  - Upserts track_cache for that race_rider_id so the live race_day page can render updated tracks

Run:
  source .venv/bin/activate
  python -m src.workers.gpx_worker

Notes:
- Simple polling; no queue required.
- Uses an in-memory watermark per device_id to skip rebuilds when no new points arrived.
"""

import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from src.db.models import SessionLocal, Point, RaceRider, TrackCache, init_db
from src.utils.gpx import build_geojson_for_device

SLEEP_SEC = 5.0  # poll frequency; adjust as needed

def _distinct_devices(session) -> list[str]:
    """
    Return list of distinct device_id values in points.
    """
    rows = session.execute(select(Point.device_id).group_by(Point.device_id)).scalars().all()
    return [r for r in rows if r]


def _latest_race_rider_id(session, device_id: str) -> Optional[int]:
    """
    Look up the newest race_rider.id associated with a device_id.
    The latest is defined as the highest race_rider_id (covers reassignments).
    """
    return (
        session.execute(
            select(RaceRider.id)
            .where(RaceRider.device_id == device_id)
            .order_by(RaceRider.id.desc())
            .limit(1)
        )
        .scalar_one_or_none()
    )

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
                # track_cache row (one per rider entry) is updated.
                race_rider_id = _latest_race_rider_id(session, did)
                if race_rider_id is None:
                    # Device has points but is not linked to a race rider; skip for now.
                    continue
                
                # TODO : if there is a race_rider_id in the track_hist then then the rider is finished the race
                # so skip the building of the geojson and rather use the track_hist geojson to update the track_cache

                # Build GeoJSON in-memory (save=False) so we can store it directly in track_cache.
                ok, geojson_or_err = build_geojson_for_device(
                    device_id=did, session=session, save=False
                )
                if not ok:
                    print(f"[gpx_worker] {geojson_or_err}")
                    continue

                geojson_str = geojson_or_err
                now = datetime.now(timezone.utc)

                # Upsert: replace existing cache for this race_rider_id or create a new row.
                cache_row = session.get(TrackCache, race_rider_id)
                if cache_row:
                    cache_row.geojson = geojson_str
                    cache_row.updated_at = now
                else:
                    session.add(
                        TrackCache(
                            race_rider_id=race_rider_id,
                            geojson=geojson_str,
                            updated_at=now,
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
