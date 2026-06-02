"""
RFID timing worker (DB-polling).

Every SLEEP_SEC:
  - Reads unprocessed ingest_rfid rows
  - Maps each EPC to a Device.epc_id
  - Finds the latest RaceRider for that device_id
  - Updates start/finish RFID and Pi timing epoch fields
  - Flags ambiguous extra RFID reads for later review
  - Marks each ingest_rfid row processed so it is not handled twice

Run:
  source .venv/bin/activate
  python -m src.workers.rfid_worker

Notes:
- "RFID time" comes from the reader event timestamp.
- "Pi time" uses the web app/server received_at_epoch for the same RFID row.
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
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.db.models import SessionLocal, init_db, IngestRfid, Device, RaceRider, Category, Route, Race
from src.utils.time import datetime_to_epoch, epoch_to_datetime

# ---- Configuration ----
BATCH_SIZE = 200
SLEEP_SEC = 30.0
MATCH_WINDOW_SEC = 5
START_WINDOW_SEC = 60 * 60


def _average_epoch(existing_epoch: Optional[int], new_epoch: Optional[int]) -> Optional[int]:
    """
    Average two epoch values while tolerating one missing value.

    Input Args:
      existing_epoch: currently stored epoch value.
      new_epoch: new epoch value from the RFID event.

    Output:
      rounded average epoch when both values exist, otherwise the present value.
    """
    if existing_epoch is None:
        return new_epoch
    if new_epoch is None:
        return existing_epoch
    return int(round((int(existing_epoch) + int(new_epoch)) / 2))


def _is_within_window(candidate_epoch: Optional[int], reference_epoch: Optional[int], window_sec: int) -> bool:
    """
    Check whether a candidate epoch is within a symmetric time window.

    Input Args:
      candidate_epoch: event epoch being checked.
      reference_epoch: existing/reference epoch.
      window_sec: allowed absolute difference in seconds.

    Output:
      True when both epochs exist and abs(candidate-reference) <= window_sec.
    """
    if candidate_epoch is None or reference_epoch is None:
        return False
    return abs(int(candidate_epoch) - int(reference_epoch)) <= window_sec


def _get_unprocessed_rfid_rows(session) -> List[IngestRfid]:
    """
    Fetch a batch of RFID rows that have not been processed by this worker.

    Input Args:
      session: active SQLAlchemy session.

    Output:
      List of IngestRfid rows ordered by id ascending.
    """
    return (
        session.execute(
            select(IngestRfid)
            .where(IngestRfid.processed_at_epoch.is_(None))
            .order_by(IngestRfid.id.asc())
            .limit(BATCH_SIZE)
        )
        .scalars()
        .all()
    )


def _find_device_id_for_epc(session, epc: str) -> Optional[str]:
    """
    Find the registered device_id for an RFID EPC.

    Input Args:
      session: active SQLAlchemy session.
      epc: RFID EPC/tag value.

    Output:
      Device.id when the EPC is registered, otherwise None.
    """
    return (
        session.execute(
            select(Device.id)
            .where(Device.epc_id == epc)
            .limit(1)
        )
        .scalar_one_or_none()
    )


def _latest_race_rider_for_device(session, device_id: str) -> Tuple[Optional[RaceRider], Optional[int]]:
    """
    Find the latest RaceRider linked to a device and its race start epoch.

    Input Args:
      session: active SQLAlchemy session.
      device_id: device identifier from devices.id.

    Output:
      (RaceRider row, race starts_at_epoch) or (None, None) when no entry exists.
    """
    row = (
        session.execute(
            select(RaceRider, Race.starts_at_epoch)
            .join(Category, RaceRider.category_id == Category.id)
            .join(Route, Category.route_id == Route.id)
            .join(Race, Route.race_id == Race.id)
            .where(RaceRider.device_id == device_id)
            .order_by(RaceRider.id.desc())
            .limit(1)
        )
        .first()
    )
    if not row:
        return None, None
    return row[0], row[1]


def _is_in_start_window(event_epoch: Optional[int], race_start_epoch: Optional[int]) -> bool:
    """
    Check whether an RFID event is within the race start timing window.

    Input Args:
      event_epoch: RFID reader event epoch.
      race_start_epoch: Race.starts_at_epoch for the latest race rider.

    Output:
      True when the event is within one hour either side of race start.
    """
    return _is_within_window(event_epoch, race_start_epoch, START_WINDOW_SEC)


def _set_start_times(race_rider: RaceRider, rfid_epoch: int, received_epoch: Optional[int]) -> None:
    """
    Set or merge a RaceRider start RFID/Pi timing pair.

    Input Args:
      race_rider: RaceRider row to update.
      rfid_epoch: RFID reader event epoch.
      received_epoch: server receipt epoch for the RFID event.

    Output:
      None; mutates race_rider timing fields.
    """
    race_rider.start_time_rfid_epoch = _average_epoch(race_rider.start_time_rfid_epoch, rfid_epoch)
    race_rider.start_time_pi_epoch = _average_epoch(race_rider.start_time_pi_epoch, received_epoch)
    race_rider.start_time_rfid = (
        epoch_to_datetime(race_rider.start_time_rfid_epoch, tz_name="UTC")
        if race_rider.start_time_rfid_epoch is not None
        else None
    )
    race_rider.start_time_pi = (
        epoch_to_datetime(race_rider.start_time_pi_epoch, tz_name="UTC")
        if race_rider.start_time_pi_epoch is not None
        else None
    )


def _set_finish_times(race_rider: RaceRider, rfid_epoch: int, received_epoch: Optional[int]) -> None:
    """
    Set or merge a RaceRider finish RFID/Pi timing pair.

    Input Args:
      race_rider: RaceRider row to update.
      rfid_epoch: RFID reader event epoch.
      received_epoch: server receipt epoch for the RFID event.

    Output:
      None; mutates race_rider timing fields.
    """
    race_rider.finish_time_rfid_epoch = _average_epoch(race_rider.finish_time_rfid_epoch, rfid_epoch)
    race_rider.finish_time_pi_epoch = _average_epoch(race_rider.finish_time_pi_epoch, received_epoch)
    race_rider.finish_time_rfid = (
        epoch_to_datetime(race_rider.finish_time_rfid_epoch, tz_name="UTC")
        if race_rider.finish_time_rfid_epoch is not None
        else None
    )
    race_rider.finish_time_pi = (
        epoch_to_datetime(race_rider.finish_time_pi_epoch, tz_name="UTC")
        if race_rider.finish_time_pi_epoch is not None
        else None
    )


def _mark_processed(row: IngestRfid, processed_epoch: int, process_error: Optional[str] = None) -> None:
    """
    Mark an RFID ingest row as processed by this worker.

    Input Args:
      row: IngestRfid row to update.
      processed_epoch: worker processing epoch.
      process_error: optional skip/error reason.

    Output:
      None; mutates row bookkeeping fields.
    """
    row.processed_at_epoch = processed_epoch
    row.process_error = process_error


def _process_rfid_row(session, row: IngestRfid, processed_epoch: int) -> str:
    """
    Process one RFID ingest row into race rider timing fields.

    Input Args:
      session: active SQLAlchemy session.
      row: IngestRfid row to process.
      processed_epoch: shared worker processing epoch.

    Output:
      short result label for logs.
    """
    if row.time_stamp_epoch is None:
        _mark_processed(row, processed_epoch, "Missing RFID timestamp")
        return "missing_timestamp"

    device_id = _find_device_id_for_epc(session, row.epc)
    if device_id is None:
        _mark_processed(row, processed_epoch, "No device linked to EPC")
        return "no_device"

    # race_start_epoch here is the inputted at when defining the race and is saved in the race table. It is not derived from any RFID reads
    # race_rider is the full race rider row linked to the device, which may or may not have start/finish times already
    race_rider, race_start_epoch = _latest_race_rider_for_device(session, device_id)
    if race_rider is None:
        _mark_processed(row, processed_epoch, "No race_rider linked to device")
        return "no_race_rider"

    # Start reads are only accepted inside the race start window. If a start
    # already exists, a repeated read must also be within 5 seconds of it.
    start_missing = race_rider.start_time_rfid_epoch is None
    start_repeat = _is_within_window(row.time_stamp_epoch, race_rider.start_time_rfid_epoch, MATCH_WINDOW_SEC)
    if (start_missing or start_repeat) and _is_in_start_window(row.time_stamp_epoch, race_start_epoch):
        _set_start_times(race_rider, row.time_stamp_epoch, row.received_at_epoch)
        _mark_processed(row, processed_epoch)
        return "start"

    if race_rider.finish_time_rfid_confirmed:
        race_rider.multiple_rfid_flag = False
        _mark_processed(row, processed_epoch, "RFID finish timing already confirmed")
        return "finish_confirmed"

    # Finish reads are accepted once outside the start window. Extra reads within
    # five seconds are averaged into the existing finish timing pair.
    finish_missing = race_rider.finish_time_rfid_epoch is None
    finish_repeat = _is_within_window(row.time_stamp_epoch, race_rider.finish_time_rfid_epoch, MATCH_WINDOW_SEC)
    finish_first_read = finish_missing and not _is_in_start_window(row.time_stamp_epoch, race_start_epoch)
    if finish_first_read or finish_repeat:
        _set_finish_times(race_rider, row.time_stamp_epoch, row.received_at_epoch)
        _mark_processed(row, processed_epoch)
        return "finish"

    race_rider.multiple_rfid_flag = True
    _mark_processed(row, processed_epoch, "RFID read did not match start/finish windows")
    return "multiple_flag"


def _process_batch_once() -> int:
    """
    Process one batch of unprocessed RFID ingest rows.

    Returns:
      number of IngestRfid rows processed this call (0 if none).
    """
    session = SessionLocal()
    try:
        rows = _get_unprocessed_rfid_rows(session)
        if not rows:
            return 0

        processed_epoch = datetime_to_epoch(datetime.now(timezone.utc))
        result_counts = {}
        for row in rows:
            try:
                result = _process_rfid_row(session, row, processed_epoch)
            except Exception as e:
                result = "error"
                _mark_processed(row, processed_epoch, str(e)[:500])
            result_counts[result] = result_counts.get(result, 0) + 1

        session.commit()
        print(f"[rfid_worker] processed {len(rows)} rows: {result_counts}")
        return len(rows)

    except SQLAlchemyError as e:
        session.rollback()
        print(f"[rfid_worker] DB error: {e}")
        return 0
    finally:
        session.close()


def main():
    """Entry point for the background RFID timing worker."""
    init_db()
    print("[rfid_worker] started (polling IngestRfid)")
    while True:
        n = _process_batch_once()
        if n == 0:
            time.sleep(SLEEP_SEC)


if __name__ == "__main__":
    main()
