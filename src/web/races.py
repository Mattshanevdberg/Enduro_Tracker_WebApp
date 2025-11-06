"""
Routes to create/edit a race, upload/remove GPX per category, preview the route,
and manage the RaceRider assignments for a selected category.

Paths (main ones)
-----------------
GET  /races/new                 -> Create new race page (empty form)
POST /races/save                -> Save new or existing race
GET  /races/<race_id>/edit      -> Edit page; choose category via ?category=Professional
POST /races/<race_id>/route/upload   -> Upload GPX for selected category
POST /races/<race_id>/route/remove   -> Remove GPX for selected category
GET  /races/<race_id>/route/geojson  -> Return GeoJSON for selected category (map uses this)

POST /races/<race_id>/riders/add              -> Add a RaceRider row for this category
POST /races/<race_id>/riders/<entry_id>/edit  -> Update an existing RaceRider row
POST /races/<race_id>/riders/<entry_id>/remove-> Delete an existing RaceRider row
"""

from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, request, render_template, redirect, url_for, Response, jsonify
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from src.db.models import (
    SessionLocal, init_db,
    Race, Route, Category, Rider, Device, RaceRider
)
from src.db.models import config as app_config  # already loaded from config.yaml
from src.utils.gpx import gpx_to_geojson

bp_races = Blueprint("races", __name__, url_prefix="/races")

# Helper: read allowed categories from config.yaml
ALLOWED_CATEGORIES = app_config.get("categories", ["Professional", "Open", "Junior"])


def _parse_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """
    Build a timezone-aware UTC datetime from separate date and time strings.
    Empty strings return None.

    Expected formats:
      date: YYYY-MM-DD
      time: HH:MM  (24h)
    """
    if not date_str or not time_str:
        return None
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        # Store as UTC; if you want local timezone, adjust here
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _find_or_create_route_for_category(session, race_id: int, category_name: str) -> tuple[Route, Category]:
    """
    Get or create the (Route, Category) pair for (race_id, category_name).

    We keep one Route per category in a race, and one Category row that records the name.
    """
    # First, find any existing Route rows for the race that have a Category with this name
    route = (
        session.query(Route)
        .join(Category, Category.route_id == Route.id)
        .filter(Route.race_id == race_id, Category.name == category_name)
        .one_or_none()
    )
    if route:
        cat = session.query(Category).filter(Category.route_id == route.id, Category.name == category_name).one()
        return route, cat

    # Else, create a new empty Route and attached Category
    route = Route(race_id=race_id, geojson=None, gpx=None)
    session.add(route)
    session.flush()  # get route.id
    cat = Category(route_id=route.id, name=category_name)
    session.add(cat)
    session.flush()
    return route, cat


@bp_races.route("/new", methods=["GET"])
def new_race():
    """
    Render a blank "New Race" page.
    The page lets you fill race fields and also pick a category context.
    """
    session = SessionLocal()
    try:
        return render_template(
            "race_form.html",
            race=None,
            categories=ALLOWED_CATEGORIES,
            selected_category=ALLOWED_CATEGORIES[0],
            riders=[],
            devices=[],
            race_riders=[],
            message=None,
            success=None,
        )
    finally:
        session.close()


@bp_races.route("/save", methods=["POST"])
def save_race():
    """
    Create or update a race. Only 'name' is required.
    - If 'race_id' is present, we update that race.
    - Otherwise we create a new one.
    """
    session = SessionLocal()
    try:
        race_id = request.form.get("race_id")
        name = (request.form.get("name") or "").strip()
        website = (request.form.get("website") or "").strip() or None
        start_date = (request.form.get("start_date") or "").strip()
        start_time = (request.form.get("start_time") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        active = True if request.form.get("active") == "on" else False

        if not name:
            return Response("Race name is required.", status=400)

        starts_at = _parse_datetime(start_date, start_time)

        if race_id:
            race = session.query(Race).get(int(race_id))
            if not race:
                return Response("Race not found.", status=404)
            race.name = name
            race.website = website
            race.description = description
            race.starts_at = starts_at
            race.active = active
        else:
            race = Race(
                name=name,
                website=website,
                description=description,
                starts_at=starts_at,
                active=active,
            )
            session.add(race)
            session.flush()  # obtain race.id

        session.commit()
        # After save, go to edit page (so you can upload GPX and manage riders)
        return redirect(url_for("races.edit_race", race_id=race.id, category=ALLOWED_CATEGORIES[0]))
    except SQLAlchemyError as e:
        session.rollback()
        return Response(f"DB error: {e}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/edit", methods=["GET"])
def edit_race(race_id: int):
    """
    Edit page for a race. The UI is scoped to a selected category (via ?category=...).
    """
    session = SessionLocal()
    try:
        race = session.query(Race).get(race_id)
        if not race:
            return Response("Race not found.", status=404)

        # Which category tab is selected in the UI?
        selected_category = request.args.get("category") or ALLOWED_CATEGORIES[0]
        if selected_category not in ALLOWED_CATEGORIES:
            selected_category = ALLOWED_CATEGORIES[0]

        # Make sure we have a (Route, Category) record pair for this selection
        route, cat = _find_or_create_route_for_category(session, race.id, selected_category)
        session.commit()  # persist any just-created rows

        # Build lists for selectors
        riders = session.query(Rider).order_by(Rider.name.asc()).all()
        devices = session.query(Device).order_by(Device.id.asc()).all()

        # Existing entries (riders already added for this category)
        rrows = (
            session.query(RaceRider)
            .filter(RaceRider.category_id == cat.id)
            .order_by(RaceRider.id.asc())
            .all()
        )

        # Map: rider_id -> last device they used (from any past RaceRider row)
        # We simply take the highest id as "most recent".
        last_device_by_rider = {}
        for rid in [r.id for r in riders]:
            last = (
                session.query(RaceRider)
                .filter(RaceRider.rider_id == rid)
                .order_by(RaceRider.id.desc())
                .first()
            )
            last_device_by_rider[rid] = last.device_id if last else None

        # Riders not yet selected for THIS race/category (for the add form dropdown)
        selected_rider_ids = {row.rider_id for row in rrows}
        available_riders = [r for r in riders if r.id not in selected_rider_ids]

        # GeoJSON preview for the selected category (may be None)
        geojson = route.geojson

        return render_template(
            "race_form.html",
            race=race,
            categories=ALLOWED_CATEGORIES,
            selected_category=selected_category,
            route=route,
            geojson=geojson,
            riders=available_riders,
            devices=devices,
            race_riders=rrows,
            last_device_by_rider=last_device_by_rider,
            message=None,
            success=None,
        )
    finally:
        session.close()


@bp_races.route("/<int:race_id>/route/upload", methods=["POST"])
def upload_gpx(race_id: int):
    """
    Upload a GPX file for the selected category and store both GPX and GeoJSON on Route.
    Ensures one route per (race, category). Replaces any previous GPX for that category.
    """
    session = SessionLocal()
    try:
        category_name = request.form.get("category") or ALLOWED_CATEGORIES[0]
        if category_name not in ALLOWED_CATEGORIES:
            return Response("Invalid category.", status=400)

        file = request.files.get("gpx_file")
        if not file or file.filename == "":
            return Response("Please choose a GPX file.", status=400)

        gpx_text = file.read().decode("utf-8", errors="ignore")
        ok, result = gpx_to_geojson(gpx_text)
        if not ok:
            return Response(result, status=400)

        # Find or create route/category for this race + category
        route, cat = _find_or_create_route_for_category(session, race_id, category_name)

        # Store both
        route.gpx = gpx_text
        route.geojson = result
        session.commit()

        return redirect(url_for("races.edit_race", race_id=race_id, category=category_name))
    except SQLAlchemyError as e:
        session.rollback()
        return Response(f"DB error: {e}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/route/remove", methods=["POST"])
def remove_gpx(race_id: int):
    """
    Remove the GPX/GeoJSON for the selected category (does not delete the route row entirely).
    """
    session = SessionLocal()
    try:
        category_name = request.form.get("category") or ALLOWED_CATEGORIES[0]
        route = (
            session.query(Route)
            .join(Category, Category.route_id == Route.id)
            .filter(Route.race_id == race_id, Category.name == category_name)
            .one_or_none()
        )
        if not route:
            return Response("No route for that category.", status=404)

        route.gpx = None
        route.geojson = None
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race_id, category=category_name))
    except SQLAlchemyError as e:
        session.rollback()
        return Response(f"DB error: {e}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/route/geojson", methods=["GET"])
def route_geojson(race_id: int):
    """
    Provide the GeoJSON for the selected category, so the map can fetch it with AJAX.
    """
    session = SessionLocal()
    try:
        category_name = request.args.get("category") or ALLOWED_CATEGORIES[0]
        row = (
            session.query(Route.geojson)
            .join(Category, Category.route_id == Route.id)
            .filter(Route.race_id == race_id, Category.name == category_name)
            .one_or_none()
        )
        gj = row[0] if row else None
        if not gj:
            return jsonify({"type": "FeatureCollection", "features": []})
        return Response(gj, mimetype="application/json")
    finally:
        session.close()


@bp_races.route("/<int:race_id>/riders/add", methods=["POST"])
def add_race_rider(race_id: int):
    """
    Add a rider to this race for the selected category. We look up the Category row by name.
    """
    session = SessionLocal()
    try:
        category_name = request.form.get("category") or ALLOWED_CATEGORIES[0]
        rider_id = int(request.form.get("rider_id"))
        device_id = (request.form.get("device_id") or "").strip()

        # find category.id for (race, category_name)
        route, cat = _find_or_create_route_for_category(session, race_id, category_name)

        # save RaceRider
        rr = RaceRider(rider_id=rider_id, device_id=device_id, category_id=cat.id, active=True, recording=True)
        session.add(rr)
        session.commit()

        return redirect(url_for("races.edit_race", race_id=race_id, category=category_name))
    except SQLAlchemyError as e:
        session.rollback()
        return Response(f"DB error: {e}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/riders/<int:entry_id>/edit", methods=["POST"])
def edit_race_rider(race_id: int, entry_id: int):
    """
    Update device/flags for an existing RaceRider entry.
    """
    session = SessionLocal()
    try:
        category_name = request.form.get("category") or ALLOWED_CATEGORIES[0]
        device_id = (request.form.get("device_id") or "").strip()
        active = True if request.form.get("active") == "on" else False
        recording = True if request.form.get("recording") == "on" else False

        rr = session.query(RaceRider).get(entry_id)
        if not rr:
            return Response("RaceRider not found.", status=404)

        rr.device_id = device_id
        rr.active = active
        rr.recording = recording
        session.commit()

        return redirect(url_for("races.edit_race", race_id=race_id, category=category_name))
    except SQLAlchemyError as e:
        session.rollback()
        return Response(f"DB error: {e}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/riders/<int:entry_id>/remove", methods=["POST"])
def remove_race_rider(race_id: int, entry_id: int):
    """
    Delete a RaceRider row.
    """
    session = SessionLocal()
    try:
        category_name = request.form.get("category") or ALLOWED_CATEGORIES[0]
        rr = session.query(RaceRider).get(entry_id)
        if not rr:
            return Response("RaceRider not found.", status=404)
        session.delete(rr)
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race_id, category=category_name))
    except SQLAlchemyError as e:
        session.rollback()
        return Response(f"DB error: {e}", status=500)
    finally:
        session.close()
