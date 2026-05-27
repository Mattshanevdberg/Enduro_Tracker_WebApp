"""
RFID ingest record viewer.

- GET /rfid/ -> list recent RFID ingest rows with simple column filters.

Notes
-----
* This page is a lightweight operations view for confirming reader uploads.
* Filtering is intentionally server-side so large RFID tables do not need to be
  fully loaded into the browser.
"""

from flask import Blueprint, render_template, request
from sqlalchemy.exc import SQLAlchemyError

from src.db.models import SessionLocal, IngestRfid
from src.utils.time import epoch_to_datetime, iso_to_epoch

bp_rfid = Blueprint("rfid", __name__, url_prefix="/rfid")

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


def _parse_optional_int(value):
    """
    Parse an optional integer request value.

    Input Args:
      value: raw string value from request.args.

    Output:
      int value, or None when the input is empty.

    Raises:
      ValueError when the input is present but cannot be converted to int.
    """
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    return int(value)


def _parse_limit(value):
    """
    Parse the requested row limit and clamp it to the supported range.

    Input Args:
      value: raw limit value from request.args.

    Output:
      integer row limit between 1 and MAX_LIMIT.
    """
    parsed = _parse_optional_int(value)
    if parsed is None:
        return DEFAULT_LIMIT
    return max(1, min(parsed, MAX_LIMIT))


def _datetime_filter_to_epoch(value):
    """
    Parse an optional datetime filter value into epoch seconds.

    Input Args:
      value: raw datetime string from a datetime-local filter input.

    Output:
      epoch seconds or None for an empty input.

    Raises:
      ValueError when the input is present but cannot be parsed as a local datetime.
    """
    if value is None or value.strip() == "":
        return None
    return iso_to_epoch(value.strip())


@bp_rfid.route("/", methods=["GET"])
def rfid_index():
    """
    Render RFID ingest rows with optional column filters.

    Query string filters:
      - id: exact ingest_rfid id
      - epc: partial, case-insensitive EPC match
      - reader_id: partial, case-insensitive reader id match
      - ant: partial, case-insensitive antenna match
      - time_from: minimum reader timestamp as local datetime
      - time_to: maximum reader timestamp as local datetime
      - received_from: minimum server receipt timestamp as local datetime
      - received_to: maximum server receipt timestamp as local datetime
      - limit: maximum rows to show, default 200 and capped at 1000

    Output:
      Rendered rfid_view.html with rows, filters, and optional message.
    """
    filters = {
        "id": (request.args.get("id") or "").strip(),
        "epc": (request.args.get("epc") or "").strip(),
        "reader_id": (request.args.get("reader_id") or "").strip(),
        "ant": (request.args.get("ant") or "").strip(),
        "time_from": (request.args.get("time_from") or "").strip(),
        "time_to": (request.args.get("time_to") or "").strip(),
        "received_from": (request.args.get("received_from") or "").strip(),
        "received_to": (request.args.get("received_to") or "").strip(),
        "limit": (request.args.get("limit") or str(DEFAULT_LIMIT)).strip(),
    }

    session = SessionLocal()
    message = None
    success = None

    try:
        query = session.query(IngestRfid)

        ingest_id = _parse_optional_int(filters["id"])
        time_from = _datetime_filter_to_epoch(filters["time_from"])
        time_to = _datetime_filter_to_epoch(filters["time_to"])
        received_from = _datetime_filter_to_epoch(filters["received_from"])
        received_to = _datetime_filter_to_epoch(filters["received_to"])
        limit = _parse_limit(filters["limit"])

        if ingest_id is not None:
            query = query.filter(IngestRfid.id == ingest_id)
        if filters["epc"]:
            query = query.filter(IngestRfid.epc.ilike(f"%{filters['epc']}%"))
        if filters["reader_id"]:
            query = query.filter(IngestRfid.reader_id.ilike(f"%{filters['reader_id']}%"))
        if filters["ant"]:
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
            .order_by(IngestRfid.received_at_epoch.desc().nullslast(), IngestRfid.id.desc())
            .limit(limit)
            .all()
        )

        for row in rows:
            row.reader_time = epoch_to_datetime(row.time_stamp_epoch) if row.time_stamp_epoch is not None else None
            row.received_at = epoch_to_datetime(row.received_at_epoch) if row.received_at_epoch is not None else None

        return render_template(
            "rfid_view.html",
            rows=rows,
            filters=filters,
            message=message,
            success=success,
            max_limit=MAX_LIMIT,
        )
    except ValueError:
        session.rollback()
        return render_template(
            "rfid_view.html",
            rows=[],
            filters=filters,
            message="Numeric filters must be whole numbers.",
            success=False,
            max_limit=MAX_LIMIT,
        ), 400
    except SQLAlchemyError as e:
        session.rollback()
        return render_template(
            "rfid_view.html",
            rows=[],
            filters=filters,
            message=f"DB error: {e}",
            success=False,
            max_limit=MAX_LIMIT,
        ), 500
    finally:
        session.close()
