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
import os
import yaml
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from flask import Blueprint, request, jsonify
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

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

from src.utils.gpx import _iso8601_utc  # reuse time formatter for GPX output

# ---------------------------------------------------------------------------
# Helpers to parse text logs and build GPX/GeoJSON strings
# ---------------------------------------------------------------------------

def _parse_text_fixes(raw_text: str):
    """
    Parse line-delimited JSON fixes from raw text.

    Keeps rows that decode to JSON objects and contain non-null utc/lat/lon.
    Drops malformed or incomplete rows.

    Args:
        raw_text (str): Raw text payload containing one JSON object per line.

    Returns:
        list[dict]: Cleaned fixes with utc/lat/lon and optional fields; bad rows removed.
    """
    fixes = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            utc = obj.get("utc")
            lat = obj.get("lat")
            lon = obj.get("lon")
            if utc is None or lat is None or lon is None:
                continue
            fixes.append({
                "utc": utc,
                "lat": lat,
                "lon": lon,
                "alt": obj.get("alt"),
                "sog": obj.get("sog"),
                "cog": obj.get("cog"),
                "fx": obj.get("fx"),
                "hdop": obj.get("hdop"),
                "nsat": obj.get("nsat"),
            })
        except Exception:
            # bad line: skip
            continue
    return fixes


def _build_gpx_string(fixes, creator: str = "EnduroTracker") -> str:
    """
    Build a GPX 1.1 XML string from cleaned fixes (list of dicts).

    Args:
        fixes (list[dict]): Cleaned fixes containing at least utc/lat/lon.
        creator (str): Creator metadata for the GPX file.

    Returns:
        str: GPX XML string.
    """
    gpx_ns = "http://www.topografix.com/GPX/1/1"
    xsi_ns = "http://www.w3.org/2001/XMLSchema-instance"
    schema_loc = "http://www.topografix.com/GPX/1/1/gpx.xsd"
    ET.register_namespace("", gpx_ns)
    ET.register_namespace("xsi", xsi_ns)

    gpx = ET.Element(
        ET.QName(gpx_ns, "gpx"),
        {
            ET.QName(xsi_ns, "schemaLocation"): f"{gpx_ns} {schema_loc}",
            "version": "1.1",
            "creator": creator,
        },
    )

    meta = ET.SubElement(gpx, ET.QName(gpx_ns, "metadata"))
    ET.SubElement(meta, ET.QName(gpx_ns, "time")).text = _iso8601_utc(int(fixes[0]["utc"]))

    trk = ET.SubElement(gpx, ET.QName(gpx_ns, "trk"))
    ET.SubElement(trk, ET.QName(gpx_ns, "name")).text = "Log Track"
    trkseg = ET.SubElement(trk, ET.QName(gpx_ns, "trkseg"))

    for p in fixes:
        pt = ET.SubElement(
            trkseg,
            ET.QName(gpx_ns, "trkpt"),
            {"lat": f"{float(p['lat']):.6f}", "lon": f"{float(p['lon']):.6f}"}
        )
        if p.get("alt") is not None:
            ET.SubElement(pt, ET.QName(gpx_ns, "ele")).text = f"{float(p['alt']):.1f}"
        if p.get("utc") is not None:
            ET.SubElement(pt, ET.QName(gpx_ns, "time")).text = _iso8601_utc(int(p["utc"]))

    return ET.tostring(gpx, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _build_geojson_string(fixes) -> str:
    """
    Build a GeoJSON LineString string from cleaned fixes.

    Args:
        fixes (list[dict]): Cleaned fixes containing at least lat/lon.

    Returns:
        str: Compact GeoJSON FeatureCollection as a string.
    """
    coords = [[float(p["lon"]), float(p["lat"])] for p in fixes]
    gj = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"src": "text_log"},
                "geometry": {"type": "LineString", "coordinates": coords},
            }
        ],
    }
    return json.dumps(gj, separators=(",", ":"))


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
      200 with {"accepted_bytes": N, "valid_fixes": M, "fixes_gpx": "...", "fixes_geojson": "..."}
      400 if no payload was provided
      422 if no valid fixes were found

    Input Args (HTTP):
      - device_id: optional (form or query)
      - Body: multipart/form-data file field "file", or raw text/plain/octet-stream body.

    Output:
      JSON containing counts and in-memory GPX/GeoJSON strings.
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

    # 4) Build GPX/GeoJSON strings in-memory (no disk writes).
    fixes_gpx = _build_gpx_string(fixes, creator=f"EnduroTracker {device_id}")
    fixes_geojson = _build_geojson_string(fixes)

    # 5) find the last race_rider id to associated with the device_id, this involves filter the race_riders table by device_id and finding the last one
    race_rider_id = None
    if device_id:
        session = SessionLocal()
        try:
            race_rider_id = session.execute(
                select(RaceRider.id)
                .where(RaceRider.device_id == device_id)
                .order_by(RaceRider.id.desc())
                .limit(1)
            ).scalar_one_or_none()
        finally:
            session.close()

    # 6) save fixes_gpx and fixes_geojson to the track_hist table. Use the found race_rider_id when logging it
    if race_rider_id:
        session = SessionLocal()
        try:
            session.add(TrackHist(race_rider_id=race_rider_id, geojson=fixes_geojson, gpx=fixes_gpx))
            session.commit()
        except SQLAlchemyError:
            session.rollback()
        finally:
            session.close()

    return "", 200
