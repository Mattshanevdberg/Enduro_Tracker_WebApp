"""
GPX/GeoJSON utilities.

Contains:
- _iso8601_utc: epoch -> ISO8601 UTC helper.
- _parse_text_fixes: clean line-delimited JSON fixes.
- _build_gpx_string: construct GPX XML string from fixes.
- _build_geojson_string: construct GeoJSON string from fixes.
- build_gpx_for_device: build GPX file from points table.
- build_geojson_for_device: build GeoJSON from points table (optionally save).
- gpx_to_geojson: convert GPX text to GeoJSON string.

Jargon:
- GPX 1.1: an XML schema for GPS tracks. A minimal file has <gpx>, <trk>, <trkseg>, <trkpt>.
- trk: "track", trkseg: "track segment", trkpt: "track point".
"""

#### for running in vscode (comment out when on Raspberry Pi)
import sys
import os

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
####

import xml.etree.ElementTree as ET # the stdlib XML parser/builder (converts XML elements into something Python can work with)
from datetime import datetime, timezone
from typing import Tuple, List, Optional, Any

from sqlalchemy import select, asc
from src.db.models import Point, SessionLocal, init_db # init_db is only for testing here
from sqlalchemy.orm import Session

# imports for converting GPX to GeoJSON
import json
import gpxpy

# GPX namespace constants
# GPX_NS and XSI_NS are namespace URIs that uniquely identify the vocabularies used in the GPX XML schema (http://www.topografix.com/GPX/1/1) and the XML Schema Instance spec (http://www.w3.org/2001/XMLSchema-instance), respectively.
# These are used to give form to the objects that are made by the XML parser
# ie it will act like a class with attributes that match the what is in the XML schema (schema is a rule file)

# some web dev jargon to help understand:
#Think of a schema like a Python class definition: it lays out the fields, their types, and any rules.
#The vocabulary (elements and attributes listed in that schema) lines up with the class attributes you’d expect on each instance.
#An element node is then like an instance of that class: it carries actual data that must follow the schema’s rules.

GPX_NS = "http://www.topografix.com/GPX/1/1" # defines the GPX 1.1 vocabulary
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance" # defines the XML Schema 
SCHEMA_LOC = "http://www.topografix.com/GPX/1/1/gpx.xsd" # 

ET.register_namespace("", GPX_NS) # these just give them unigue identifiers for each namespace ("class")
ET.register_namespace("xsi", XSI_NS)

def _iso8601_utc(epoch: int) -> str:
    """
    Convert epoch seconds (UTC) to ISO 8601 format used in GPX, e.g. 2025-10-14T12:34:56Z
    """
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

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
            # Skip rows missing required fields or containing zeroed values (treated as invalid)
            if utc in (None, 0, 0.0) or lat in (None, 0, 0.0) or lon in (None, 0, 0.0):
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


def filter_fixes_by_window(
    fixes: List[dict],
    start_epoch: Optional[int] = None,
    finish_epoch: Optional[int] = None,
) -> List[dict]:
    """
    Trim fixes to an optional [start_epoch, finish_epoch] window.

    - Safely coerces "utc" to int; drops rows without a usable timestamp.
    - Applies start and/or finish bounds independently (one-sided windows allowed).
    """
    if start_epoch is None and finish_epoch is None:
        return fixes

    trimmed = []
    for p in fixes:
        utc_val: Any = p.get("utc")
        try:
            t_epoch = int(utc_val)
        except Exception:
            continue
        if start_epoch is not None and t_epoch < start_epoch:
            continue
        if finish_epoch is not None and t_epoch > finish_epoch:
            continue
        trimmed.append(p)
    return trimmed


def build_gpx_for_device(device_id: str, session: Session = SessionLocal, out_dir: str = "logs") -> Tuple[bool, str]:
    """
    Build (or rebuild) a GPX 1.1 file for a given device_id from the points table.

    Parameters
    ----------
    session : SessionLocal
        SQLAlchemy session bound to your database.
    device_id : str
        Device identifier (must exist in points).
    out_dir : str
        Directory to write the .gpx file (default "logs").

    Returns
    -------
    (ok, path_or_error) : (bool, str)
        True and file path when successful, otherwise False and an error message.

    Behavior
    --------
    - Queries all rows from `points` for `device_id`, ordered by t_epoch.
    - Writes/overwrites logs/<device_id>.gpx.
    - Each point becomes a <trkpt lat="" lon=""><ele>...</ele><time>...</time></trkpt>.
    - Missing elevation/time values are handled gracefully.
    """
    try:
        # Ensure output directory exists
        os.makedirs(out_dir, exist_ok=True)

        # Fetch ordered points
        rows = (
            session.execute(
                select(Point).where(Point.device_id == device_id).order_by(asc(Point.t_epoch))
            )
            .scalars()
            .all()
        )

        if not rows:
            return False, f"No points found for device_id={device_id}"

        # Create the root GPX element (like instantiating the top-level object).
        gpx = ET.Element(
            # Wrap the tag name with the GPX namespace so XML readers know its vocabulary.
            ET.QName(GPX_NS, "gpx"),
            {
                # Add the schemaLocation attribute using the xsi namespace to point at the schema file.
                ET.QName(XSI_NS, "schemaLocation"): f"{GPX_NS} {SCHEMA_LOC}",
                # Store the GPX version number on the root element.
                "version": "1.1",
                # Record which program wrote out this GPX file.
                "creator": "EnduroTracker",
            },
        )

        # Add a <metadata> child to the root (acts like adding a nested object on our GPX instance).
        meta = ET.SubElement(gpx, ET.QName(GPX_NS, "metadata"))
        # Create a <time> child under <metadata> and fill it with the timestamp of the first point.
        # SubElement is like constructing a child node attached to its parent in one call.
        # rows[0] holds the earliest point because we ordered by t_epoch (time).
        # GPX viewers often use this metadata time as the overall track start.
        ET.SubElement(meta, ET.QName(GPX_NS, "time")).text = _iso8601_utc(rows[0].t_epoch)

        # <trk> container
        trk = ET.SubElement(gpx, ET.QName(GPX_NS, "trk"))
        ET.SubElement(trk, ET.QName(GPX_NS, "name")).text = f"Track {device_id}"
        trkseg = ET.SubElement(trk, ET.QName(GPX_NS, "trkseg"))

        # Point
        for p in rows:
            # lat/lon required in GPX for trkpt; skip if missing
            if p.lat is None or p.lon is None:
                continue
            pt = ET.SubElement(trkseg, ET.QName(GPX_NS, "trkpt"), {"lat": f"{p.lat:.6f}", "lon": f"{p.lon:.6f}"})
            # Optional elevation
            if p.ele is not None:
                ET.SubElement(pt, ET.QName(GPX_NS, "ele")).text = f"{p.ele:.1f}"
            # Optional time
            if p.t_epoch is not None:
                ET.SubElement(pt, ET.QName(GPX_NS, "time")).text = _iso8601_utc(p.t_epoch)

        # Write file
        out_path = os.path.join(out_dir, f"{device_id}.gpx")
        tree = ET.ElementTree(gpx)
        tree.write(out_path, encoding="utf-8", xml_declaration=True)

        return True, out_path

    except Exception as e:
        return False, f"build_gpx_for_device error: {e}"

def build_geojson_for_device(
    device_id: str,
    session: Session = SessionLocal,
    out_dir: str = "logs",
    save: bool = True,
) -> Tuple[bool, str]:
    """
    Build (or rebuild) a GeoJSON LineString for a given device_id from the points table.

    Parameters
    ----------
    device_id : str
        Device identifier to query in the `points` table.
    session : Session
        Active SQLAlchemy session (reused for efficiency in callers).
    out_dir : str
        Directory to write the .geojson file when save=True.
    save : bool
        When True (default), write <device_id>.geojson to disk and return its path.
        When False, skip writing and return the GeoJSON string directly.

    Returns
    -------
    (ok, result) : (bool, str)
        ok=True  -> path (if saved) or JSON string (if not saved)
        ok=False -> error message
    """
    try:
        # Fetch ordered points once; avoids multiple round-trips.
        rows = (
            session.execute(
                select(Point).where(Point.device_id == device_id).order_by(asc(Point.t_epoch))
            )
            .scalars()
            .all()
        )

        if not rows:
            return False, f"No points found for device_id={device_id}"

        coords = []
        for p in rows:
            if p.lat is None or p.lon is None:
                continue
            coords.append([round(p.lon, 6), round(p.lat, 6)])

        if not coords:
            return False, f"No valid coordinates for device_id={device_id}"

        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "device_id": device_id,
                        "start_time": _iso8601_utc(rows[0].t_epoch) if rows[0].t_epoch is not None else None,
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coords,
                    },
                }
            ],
        }
        
        # Skip disk write when caller only needs the payload.
        if not save:
            return True, json.dumps(geojson, separators=(",", ":"))

        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{device_id}.geojson")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, separators=(",", ":"))

        return True, out_path

    except Exception as e:
        return False, f"build_geojson_for_device error: {e}"
    
# potentially need to add elevation data here too.
def gpx_to_geojson(gpx_text: str) -> Tuple[bool, str]:
    """
    Convert raw GPX text into a compact GeoJSON string.

    Returns
    -------
    (ok, result) : (bool, str)
      ok=True  -> result is a JSON string (GeoJSON FeatureCollection)
      ok=False -> result is an error message
    """
    try:
        gpx = gpxpy.parse(gpx_text)
        coords = []

        # Walk all tracks → segments → points and collect lon/lat (and ignore missing)
        for trk in gpx.tracks:
            for seg in trk.segments:
                for p in seg.points:
                    if p.longitude is not None and p.latitude is not None:
                        coords.append([float(p.longitude), float(p.latitude)])

        if not coords:
            return False, "No track points found in GPX."

        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"src": "gpx"},
                "geometry": {"type": "LineString", "coordinates": coords}
            }]
        }
        # compact separators to make the JSON string small
        return True, json.dumps(geojson, separators=(",", ":"))
    except Exception as e:
        return False, f"gpx_to_geojson error: {e}"

    
# test the function
# if __name__ == "__main__":
#     init_db()
#     session = SessionLocal()
#     ok, path_or_err = build_gpx_for_device(device_id="pi003", session=session, out_dir="logs")
#     if ok:
#         print(f"GPX file created at: {path_or_err}")  
