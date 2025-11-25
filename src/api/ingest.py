"""
Upload (ingest) endpoint for compact GNSS JSON.

Route:
  POST /api/v1/upload

Behavior:
  - Validates JSON shape: must include "device_id" and "f" (list of fixes).
  - Stores the original JSON string into ingest_raw for durability.
  - (Optional) Parses and writes to points table (commented stub below).
  - Returns 200 quickly with {"accepted": N}.

Notes:
  - Keep this fast; heavy work (snapping to route, caches) belongs in background jobs later.
  - Light auth: optional short token in header (X-Device-Key). Add check when you're ready.
"""

import json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from sqlalchemy.exc import SQLAlchemyError

# regular imports
import os
import yaml
from pathlib import Path

# Load configuration from yaml file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../configs/config.yaml')
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

#set globals
DATABASE_URL = config['global']['database_url']

# this is for ingesting GNSS data
from src.db.models import SessionLocal, init_db, IngestRaw
# this is for parsing the points and saving to a db table in a usable format
# parsing will be handled in a background job later
# from src.db.models import Point   # enable when parsing points now


# bp instantiates a Flask Blueprint, which is a reusable bundle of routes, error handlers, etc. for modular apps. 
# The variable bp holds that blueprint so you can register routes on it and later attach it to the main app. 
# "ingest" is the blueprint’s name—used internally (e.g., for endpoint names or URL building).
# __name__ tells Flask where this blueprint comes from so it can locate templates/static files relative to the module if needed.
# url_prefix="/api/v1" makes every route defined on the blueprint automatically live under /api/v1 when the blueprint is registered.
# a route essentially links an API endpoint (ie api/v1/upload) to a function (ie upload()) that runs when that endpoint is called
bp = Blueprint("ingest", __name__, url_prefix="/api/v1")

# Initialize DB at import/load time (safe if called multiple times)
init_db()

# @bp.route("/upload", methods=["POST"]) decorator registers the upload function as the handler for the POST /api/v1/upload route 
# (the bp blueprint supplies the /api/v1 prefix). Without that decorator, Flask wouldn’t know to call upload() for incoming requests.
@bp.route("/upload", methods=["POST"])
def upload():
    """
    Ingest compact GNSS JSON uploaded by device.

    Request JSON (example):
      {
        "device_id": "pi-001",
        "f": [[utc, lat1e5, lon1e5, alt10, sog100, cog10, fx, hdop10, nsat], ...]
      }

    Headers (optional):
      X-Device-Key: <short-token>  # enable simple auth later

    Returns:
      200: {"accepted": N} where N = number of fixes received
      400: bad/missing JSON
      422: schema invalid (missing keys or wrong types)
    """
    # 1) Parse JSON body
    data = request.get_json(silent=True) # request gives access to things client sent, get_json() parses JSON body, silent=True prevents raising error on bad JSON
    if not data:
        print("error: No JSON body")
        return "", 400

    device_id = data.get("pid")
    fixes = data.get("f")

    # 2) Validate minimal schema (device_id + list of fixes)
    if not isinstance(device_id, str) or not isinstance(fixes, list):
        print("error: Invalid schema:  require 'device_id' (str) and 'f' (list)")
        return "", 422

    # 3) OPTIONAL: check a short token header for lightweight auth
    # token = request.headers.get("X-Device-Key")
    # if not token or not verify_token(device_id, token):
    #     return jsonify({"error": "Unauthorized"}), 401

    # 4) Persist durable copy of original JSON (compact)
    #    Re-serialize to ensure it's compact and valid.
    compact_json = json.dumps({"device_id": device_id, "f": fixes}, separators=(",", ":"))

    session = SessionLocal()
    try:
        session.add(IngestRaw(device_id=device_id, payload_json=compact_json))
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        print(f"DB error: {e}")
        return "", 500
    finally:
        session.close()

    # 5) OPTIONAL: parse into points table here (this has been moved to t a background job)
    #    Example conversion from scaled ints to floats:
    # parsed_points = []
    # for row in fixes:
    #     try:
    #         utc, lat1e5, lon1e5, alt10, sog100, cog10, fx, hdop10, nsat = row
    #         parsed_points.append(Point(
    #             rider_id=None,
    #             t=int(utc),
    #             lat=float(lat1e5) / 1e5,
    #             lon=float(lon1e5) / 1e5,
    #             alt=(float(alt10) / 10.0) if alt10 is not None else None,
    #             sog=(float(sog100) / 100.0) if sog100 is not None else None,
    #             cog=(float(cog10) / 10.0) if cog10 is not None else None,
    #             fx=int(fx) if fx is not None else None,
    #             hdop=(float(hdop10) / 10.0) if hdop10 is not None else None,
    #             nsat=int(nsat) if nsat is not None else None,
    #             device_id=device_id,
    #             src="wifi"
    #         ))
    #     except Exception:
    #         # skip malformed row
    #         continue
    # if parsed_points:
    #     session = SessionLocal()
    #     try:
    #         session.add_all(parsed_points)
    #         session.commit()
    #     except SQLAlchemyError:
    #         session.rollback()
    #     finally:
    #         session.close()

    return "", 200


@bp.route("/upload-text", methods=["POST"])
def upload_text():
    """
    Ingest a raw text log payload from a device.

    Use case:
      Devices that cannot easily send JSON can instead POST a text file payload
      (content similar to logs/gnss_log_with invalid chars (\00).txt). We keep
      the content in-memory for downstream parsing and later persistence.

    Accepted shapes:
      - multipart/form-data with a file field named "file"
      - text/plain or application/octet-stream raw body

    Response:
      200 with {"accepted_bytes": N, "preview": "..."} for quick confirmation
      400 if no payload was provided
    """

        # 1) Parse JSON body
    data = request.get_json(silent=True) # request gives access to things client sent, get_json() parses JSON body, silent=True prevents raising error on bad JSON
    if not data:
        print("error: No JSON body")
        return "", 400

    device_id = data.get("pid")
    fixes = data.get("log")

    print(device_id)
    print(fixes)
    # # 1) Read bytes once, preferring a multipart file if provided.
    # incoming_file = request.files.get("file")
    # raw_bytes = incoming_file.read() if incoming_file else (request.get_data() or b"")

    # if not raw_bytes:
    #     return jsonify({"error": "No text payload received"}), 400

    # # 2) Decode safely: replace invalid/null bytes so we always get a usable string.
    # #    This is the string we can later manipulate/parse before saving to DB.
    # raw_text = raw_bytes.decode("utf-8", errors="replace")

    # # 3) Placeholder: keep the content in-memory for now. At a later stage, we can
    # #    persist raw_text to a dedicated table or queue for parsing.
    # preview = raw_text[:200]  # short preview to confirm receipt without heavy payloads

    # return jsonify({"accepted_bytes": len(raw_bytes), "preview": preview}), 200
    return "", 200
