"""
GPX builder utilities.

This module converts rows from the `points` table into a GPX 1.1 file and
saves it to the `logs/` directory.

Also Simple helpers to convert a GPX (XML text) into a GeoJSON string.

Jargon:
- GPX 1.1: an XML schema for GPS tracks. A minimal file has <gpx>, <trk>, <trkseg>, <trkpt>.
- trk: "track", trkseg: "track segment", trkpt: "track point".

Usage:
    from src.utils.gpx import build_gpx_for_device

    success, path_or_err = build_gpx_for_device(session, device_id="pi-001", out_dir="logs")
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
from typing import Tuple

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

def build_geojson_for_device(device_id: str, session: Session = SessionLocal, out_dir: str = "logs") -> Tuple[bool, str]:
    """
    Build (or rebuild) a GeoJSON LineString file for a given device_id from the points table.

    Mirrors build_gpx_for_device but outputs <device_id>.geojson.
    """
    try:
        os.makedirs(out_dir, exist_ok=True)

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
