"""
Background parser: reads unprocessed rows from IngestRaw, parses 'f' list into Point, marks processed.

Run (dev):
  source .venv/bin/activate
  python -m src.workers.parse_worker

Jargon:
- "Polling": periodically checking the DB for new work, instead of being pushed jobs.
- "Batch": process multiple rows at once to reduce overhead.
"""
#### for running in vscode (comment out when on Raspberry Pi)
import sys
import os

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
####

import json
import time
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select

from src.db.models import SessionLocal, init_db, IngestRaw, Point

# ---- Configuration ----
BATCH_SIZE = 200          # number of IngestRaw rows to process per loop
SLEEP_SEC  = 1.0          # idle sleep when no work
SCALE_INPUT = True        # True: de-scale into human floats; False: store exact raw scalars

def _convert_fix(row: List[Optional[int]], device_id: str) -> Optional[Point]:
    """
    Convert one compact fix array to a Point instance.

    Expected input order (from your device):
      [utc, lat1e6, lon1e6, alt10, sog100, cog10, fx, hdop10, nsat]

    Returns:
      Point object (not yet persisted) or None if malformed.
    """
    try:
        utc, lat1e6, lon1e6, alt10, sog100, cog10, fx, hdop10, nsat = row

        if SCALE_INPUT:
            lat  = (float(lat1e6) / 1e6) if lat1e6 is not None else None
            lon  = (float(lon1e6) / 1e6) if lon1e6 is not None else None
            ele  = (float(alt10)  / 10.0) if alt10  is not None else None
            sog  = (float(sog100) / 100.0) if sog100 is not None else None
            cog  = (float(cog10)  / 10.0) if cog10  is not None else None
            hdop = (float(hdop10) / 10.0) if hdop10 is not None else None
            t_epoch = int(utc)
        else:
            # Store scaled ints exactly as provided (least bytes). Adjust Point columns to Integer if you do this.
            lat, lon, ele, sog, cog, hdop = lat1e6, lon1e6, alt10, sog100, cog10, hdop10
            t_epoch = int(utc)

        # Defensive checks (drop malformed)
        if lat is None or lon is None or t_epoch is None:
            return None

        return Point(
            device_id=device_id,
            t_epoch=t_epoch,
            lat=lat, lon=lon, ele=ele,
            sog=sog, cog=cog, hdop=hdop,
            fx=int(fx) if fx is not None else None,
            nsat=int(nsat) if nsat is not None else None,
            # received_at auto-populates with server time
        )
    except Exception:
        # Any parse error: drop this fix
        return None

def _process_batch_once() -> int:
    """
    Parse one batch of unprocessed IngestRaw rows:
      - fetch rows where processed_at IS NULL
      - for each row: parse payload_json -> Point[]
      - bulk insert Point
      - mark IngestRaw.processed_at (and parse_error if needed)
    Returns:
      number of IngestRaw rows processed this call (0 if none)
    """
    session = SessionLocal()
    try:
        rows: List[IngestRaw] = (
            session.execute(
                select(IngestRaw)
                .where(IngestRaw.processed_at.is_(None))
                .order_by(IngestRaw.id.asc())
                .limit(BATCH_SIZE)
            )
            .scalars()
            .all()
        )

        if not rows:
            return 0

        now = datetime.now(timezone.utc)

        # Gather Point to insert
        to_insert: List[Point] = []
        for r in rows:
            try:
                data = json.loads(r.payload_json)
                device_id = data.get("device_id")
                fixes = data.get("f", [])
                for fix in fixes:
                    pt = _convert_fix(fix, device_id)
                    if pt:
                        to_insert.append(pt)
                # mark success (even if a few fixes were dropped)
                r.processed_at = now
                r.parse_error = None
            except Exception as e:
                # Keep row marked processed to avoid infinite retries,
                # but record the error so you can inspect later.
                r.processed_at = now
                r.parse_error = str(e)[:500]

        # Bulk insert Point; ignore duplicates by catching IntegrityError
        if to_insert:
            try:
                session.add_all(to_insert)
                session.commit()
            except SQLAlchemyError as e:
                # If unique constraint triggers or any error appears, rollback and try inserting one-by-one
                session.rollback()
                # Fallback slow path: insert per row, ignore duplicates
                for p in to_insert:
                    try:
                        session.add(p)
                        session.commit()
                    except SQLAlchemyError:
                        session.rollback()
                        # likely a duplicate; skip
                        continue

        # Persist processed_at / parse_error updates
        session.commit()
        return len(rows)

    except SQLAlchemyError as e:
        session.rollback()
        print(f"[parse_worker] DB error: {e}")
        return 0
    finally:
        session.close()

def main():
    """Entry point for the background parser."""
    init_db()
    print("[parse_worker] started (polling IngestRaw)")
    while True:
        n = _process_batch_once()
        if n == 0:
            time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    main()
