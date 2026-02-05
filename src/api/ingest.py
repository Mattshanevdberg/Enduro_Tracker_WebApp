"""
Upload (ingest) endpoints.

Routes:
  POST /api/v1/upload         -> Compact GNSS JSON (device_id + fixes array); stores raw payload.
  POST /api/v1/upload-text    -> Raw text log (line-delimited JSON); returns GPX/GeoJSON previews.
  POST /api/v1/upload-timing  -> Timing marker (epoch, device_id, start/finish flag, source flag).

Behavior:
  - Validate minimal schema for each route; avoid heavy work in-request.
  - GNSS upload stores a durable raw copy; text upload builds in-memory GPX/GeoJSON previews.
  - Timing markers are accepted and validated; persistence is wired in later.

Notes:
  - Keep these endpoints fast; background jobs handle heavier processing later.
  - Light auth: optional short token in header (X-Device-Key). Add check when you're ready.
"""

import json
import os
import yaml
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
import sys

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# Load configuration from yaml file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../configs/config.yaml')
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

#set globals
DATABASE_URL = config['global']['database_url']

# this is for ingesting GNSS data
from src.db.models import SessionLocal, init_db, IngestRaw, RaceRider, TrackHist
# this is for parsing the points and saving to a db table in a usable format
# parsing will be handled in a background job later
# from src.db.models import Point   # enable when parsing points now

from src.utils.gpx import _parse_text_fixes, _build_gpx_string, _build_geojson_string, filter_fixes_by_window  # reuse time formatter for GPX output
from src.utils.time import datetime_to_epoch

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

    Input Args (HTTP):
      JSON body with keys:
        - device_id: str (pid in payload)
        - f: list of compact fixes

    Output:
      Flask response with status code and minimal JSON/empty body as noted above.
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
        # Store epoch mirror immediately so we can rely on it in Phase C.
        session.add(
            IngestRaw(
                device_id=device_id,
                payload_json=compact_json,
                received_at_epoch=datetime_to_epoch(datetime.now(timezone.utc)),
            )
        )
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


@bp.route("/upload-timing", methods=["POST"])
def upload_timing():
    """
    Ingest a timing marker (official timing feed).

    Expected JSON body:
      {
        "epoch": <int>,            # required epoch seconds (UTC)
        "device_id": "<pi-id>",    # required device identifier
        "phase": "start|finish",   # required flag indicating start vs finish
        "source": "pi|rfid"        # required flag indicating where the marker came from
      }

    Persistence is deferred; this endpoint simply validates and acknowledges.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    epoch = data.get("epoch")
    device_id = data.get("device_id")
    phase = (data.get("phase") or "").strip().lower()
    source = (data.get("source") or "").strip().lower()

    # Fast validation: type + allow-list checks only.
    if not isinstance(epoch, int):
        return jsonify({"error": "epoch must be an integer epoch (seconds)"}), 422
    if not isinstance(device_id, str) or not device_id.strip():
        return jsonify({"error": "device_id must be a non-empty string"}), 422
    if phase not in {"start", "finish"}:
        return jsonify({"error": "phase must be 'start' or 'finish'"}), 422
    if source not in {"pi", "rfid"}:
        return jsonify({"error": "source must be 'pi' or 'rfid'"}), 422

    # TODO: Persist timing markers to the database once the schema is ready.
    return jsonify({
        "accepted": True,
        "epoch": epoch,
        "device_id": device_id.strip(),
        "phase": phase,
        "source": source,
    }), 200


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
      200 with {"accepted_bytes": N, "valid_fixes": M, "fixes_gpx": "...", "fixes_geojson": "..."}
      400 if no payload was provided
      422 if no valid fixes were found

    Input Args (HTTP):
      - device_id: optional (form or query)
      - Body: multipart/form-data file field "file", or raw text/plain/octet-stream body.

    Output:
      JSON containing counts and in-memory GPX/GeoJSON strings.

    Behavior:
      - Always build GPX/GeoJSON for the latest RaceRider for this device (by id desc).
      - Also build GPX/GeoJSON for any other RaceRider for this device that does NOT yet have a TrackHist row.
      - Each RaceRider uses its own start/finish window to filter fixes before serialization.
    """
        # 1) Parse JSON body
    data = request.get_json(silent=True) # request gives access to things client sent, get_json() parses JSON body, silent=True prevents raising error on bad JSON
    if not data:
        print("error: No JSON body")
        return "", 400
        
        # 2) Extract device_id and log content
    device_id = data.get("pid")
    raw_fixes = data.get("log")

    # 3) Decode safely to survive null/invalid characters, then parse fixes.
    fixes = _parse_text_fixes(raw_fixes) # TODO test that the parsing works for nulls and invalid chars
    if not fixes:
        return jsonify({"error": "No valid fixes found"}), 422

    # 4) Pull ALL race_riders for this device (id + rfid start/finish) so we can build a track per rider.
    #    We will always include the latest race_rider_id, plus any others that do not yet have TrackHist.
    race_rider_rows = []
    if device_id:
        session = SessionLocal()
        try:
            race_rider_rows = (
                session.execute(
                    select(RaceRider.id, RaceRider.start_time_rfid_epoch, RaceRider.finish_time_rfid_epoch)
                    .where(RaceRider.device_id == device_id)
                    .order_by(RaceRider.id.asc())
                )
                .all()
            )
        finally:
            session.close()

    # 5) If we have no race_riders, we cannot link to TrackHist, but we can still return 200.
    if not race_rider_rows:
        return "", 200

    # 6) Identify the latest race_rider_id (by highest id).
    latest_race_rider_id = race_rider_rows[-1][0]

    # 7) Build a set of race_rider_ids that already exist in TrackHist.
    #    These will be skipped EXCEPT for the latest_race_rider_id which is always included.
    existing_track_hist_ids = set()
    session = SessionLocal()
    try:
        existing_track_hist_ids = set(
            session.execute(
                select(TrackHist.race_rider_id)
                .where(TrackHist.race_rider_id.in_([r[0] for r in race_rider_rows]))
            )
            .scalars()
            .all()
        )
    finally:
        session.close()

    # 8) Build a list of target race_rider rows to process:
    #    - Always include latest_race_rider_id
    #    - Include any other race_rider_id not already in TrackHist
    target_rows = []
    for r in race_rider_rows:
        rr_id = r[0]
        if rr_id == latest_race_rider_id or rr_id not in existing_track_hist_ids:
            target_rows.append(r)

    # 9) For each target, filter fixes by that rider's window, build GPX/GeoJSON, and save to TrackHist.
    session = SessionLocal()
    try:
        for rr_id, start_epoch, finish_epoch in target_rows:
            # Apply the per-rider timing window to the same raw fixes.
            filtered = filter_fixes_by_window(fixes, start_epoch=start_epoch, finish_epoch=finish_epoch)
            if not filtered:
                # Skip if nothing remains after trimming (e.g., no timing or no overlap).
                continue

            # Build GPX/GeoJSON strings in-memory (no disk writes).
            fixes_gpx = _build_gpx_string(filtered, creator=f"EnduroTracker {device_id}")
            fixes_geojson = _build_geojson_string(filtered)

            # Save to TrackHist for this race_rider_id.
            session.add(
                TrackHist(
                    race_rider_id=rr_id,
                    geojson=fixes_geojson,
                    gpx=fixes_gpx,
                    raw_txt=raw_fixes,
                    updated_at_epoch=datetime_to_epoch(datetime.now(timezone.utc)),
                )
            )

        session.commit()
    except SQLAlchemyError:
        session.rollback()
    finally:
        session.close()

    return "", 200
