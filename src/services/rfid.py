"""
RFID ingest record viewer query and display services.

Functions
---------
list_filtered_rfid_records
    Apply viewer filters, limit rows, and prepare display datetimes.

The service coordinates IngestRfid durable state and viewer rules without
depending on Flask request objects, access decorators, or template rendering.
"""

from src.db.models import IngestRfid
from src.utils.rfid import (
    datetime_filter_to_epoch,
    parse_optional_int,
    parse_rfid_limit,
)
from src.utils.time import epoch_to_datetime


def list_filtered_rfid_records(session, filters: dict) -> list[IngestRfid]:
    """
    Return recent RFID records matching the supplied viewer filters.

    Input Args:
      session: active SQLAlchemy session.
      filters: normalized dictionary from normalize_rfid_filters.

    Output:
      Filtered IngestRfid rows ordered newest first and limited to the supported
      range. Each row receives reader_time and received_at display datetimes.

    Raises:
      ValueError when a numeric or datetime filter cannot be parsed.
    """
    ingest_id = parse_optional_int(filters.get("id"))
    time_from = datetime_filter_to_epoch(filters.get("time_from"))
    time_to = datetime_filter_to_epoch(filters.get("time_to"))
    received_from = datetime_filter_to_epoch(filters.get("received_from"))
    received_to = datetime_filter_to_epoch(filters.get("received_to"))
    limit = parse_rfid_limit(filters.get("limit"))

    query = session.query(IngestRfid)
    if ingest_id is not None:
        query = query.filter(IngestRfid.id == ingest_id)
    if filters.get("epc"):
        query = query.filter(IngestRfid.epc.ilike(f"%{filters['epc']}%"))
    if filters.get("reader_id"):
        query = query.filter(IngestRfid.reader_id.ilike(f"%{filters['reader_id']}%"))
    if filters.get("ant"):
        query = query.filter(IngestRfid.ant.ilike(f"%{filters['ant']}%"))
    if time_from is not None:
        query = query.filter(IngestRfid.time_stamp_epoch >= time_from)
    if time_to is not None:
        query = query.filter(IngestRfid.time_stamp_epoch <= time_to)
    if received_from is not None:
        query = query.filter(IngestRfid.received_at_epoch >= received_from)
    if received_to is not None:
        query = query.filter(IngestRfid.received_at_epoch <= received_to)

    rows = (
        query
        .order_by(
            IngestRfid.received_at_epoch.desc().nullslast(),
            IngestRfid.id.desc(),
        )
        .limit(limit)
        .all()
    )

    # Reuse the shared epoch converter for consistent local display datetimes.
    for row in rows:
        row.reader_time = (
            epoch_to_datetime(row.time_stamp_epoch)
            if row.time_stamp_epoch is not None
            else None
        )
        row.received_at = (
            epoch_to_datetime(row.received_at_epoch)
            if row.received_at_epoch is not None
            else None
        )
    return rows
