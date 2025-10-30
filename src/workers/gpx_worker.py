"""
NOT IN USE YET AND NOT TESTED.

GPX background worker (DB-polling).

Every SLEEP_SEC:
  - Enumerates distinct device_id in points
  - Builds/overwrites logs/<device_id>.gpx using src.utils.gpx.build_gpx_for_device

Run:
  source .venv/bin/activate
  python -m src.workers.gpx_worker

Notes:
- Simple, reliable; no queue required.
- You can optimize by tracking last write time and only rebuilding when new points exist.
"""

import time
from sqlalchemy import select, func
from src.db.models import SessionLocal, Points, init_db
from src.utils.gpx import build_gpx_for_device

SLEEP_SEC = 5.0  # poll frequency; adjust as needed

def _distinct_devices(session) -> list[str]:
    """
    Return list of distinct device_id values in points.
    """
    rows = session.execute(select(Points.device_id).group_by(Points.device_id)).scalars().all()
    return [r for r in rows if r]

def main():
    init_db()
    print("[gpx_worker] started (writing logs/<device_id>.gpx)")

    # (Optional) simple watermark: store last max(t_epoch) per device_id in-memory
    last_max_t = {}

    while True:
        session = SessionLocal()
        try:
            # For each device, see if new data arrived since last time
            devices = _distinct_devices(session)
            for did in devices:
                # current latest t
                tmax = session.execute(
                    select(func.max(Points.t_epoch)).where(Points.device_id == did)
                ).scalar()
                if tmax is None:
                    continue

                prev = last_max_t.get(did, -1)
                if tmax <= prev:
                    # no new data → skip build
                    continue

                ok, path_or_err = build_gpx_for_device(device_id=did, session=session, out_dir="logs")
                if ok:
                    print(f"[gpx_worker] GPX written: {path_or_err}")
                    last_max_t[did] = tmax
                else:
                    print(f"[gpx_worker] {path_or_err}")

        except Exception as e:
            print(f"[gpx_worker] unexpected error: {e}")
        finally:
            session.close()

        time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    main()
