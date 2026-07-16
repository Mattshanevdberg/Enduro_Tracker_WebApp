"""
Admin-only RFID ingest record viewer HTTP controller.

Routes
------
GET /rfid/
    Render recent RFID ingest rows with optional server-side filters.

Pure filter handling lives in src.utils.rfid, while database querying and display
preparation live in src.services.rfid. This module retains Flask HTTP glue only.
"""

from flask import Blueprint, render_template, request
from sqlalchemy.exc import SQLAlchemyError

from src.auth.decorators import admin_required
from src.db.models import SessionLocal
from src.services.rfid import list_filtered_rfid_records
from src.utils.rfid import MAX_RFID_LIMIT, normalize_rfid_filters

bp_rfid = Blueprint("rfid", __name__, url_prefix="/rfid")


@bp_rfid.route("/", methods=["GET"])
@admin_required
def rfid_index():
    """
    Render RFID ingest rows with optional query-string filters.

    Query string filters:
      id, epc, reader_id, ant, time_from, time_to, received_from,
      received_to, and limit.

    Output:
      Rendered rfid_view.html response, with HTTP 400 for invalid filters or
      HTTP 500 for an unexpected database error.
    """
    filters = normalize_rfid_filters(request.args)
    session = SessionLocal()

    try:
        rows = list_filtered_rfid_records(session, filters)
        return render_template(
            "rfid_view.html",
            rows=rows,
            filters=filters,
            message=None,
            success=None,
            max_limit=MAX_RFID_LIMIT,
        )
    except ValueError:
        session.rollback()
        return render_template(
            "rfid_view.html",
            rows=[],
            filters=filters,
            message="Numeric filters must be whole numbers.",
            success=False,
            max_limit=MAX_RFID_LIMIT,
        ), 400
    except SQLAlchemyError as error:
        session.rollback()
        return render_template(
            "rfid_view.html",
            rows=[],
            filters=filters,
            message=f"DB error: {error}",
            success=False,
            max_limit=MAX_RFID_LIMIT,
        ), 500
    finally:
        session.close()
