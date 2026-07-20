"""
Race lifecycle, route, entry, track, and timing HTTP controllers.

Routes
------
GET  /races/new
GET  /races/<race_id>/post
GET/POST /races/<race_id>/enter
GET/POST /races/<race_id>/entries/new
GET  /races/<race_id>/post-admin
GET  /races/<race_id>/results
GET  /races/<race_id>/race-rider-timings
GET  /races/<race_id>/device/<device_id>/geojson
GET  /races/<race_id>/race-rider/<race_rider_id>/track
POST /races/<race_id>/race-rider/<race_rider_id>/manual-times
POST /races/<race_id>/race-rider/<race_rider_id>/confirm-finish
POST /races/save
GET  /races/<race_id>/edit
POST /races/<race_id>/routes/add
POST /races/<race_id>/routes/<route_id>/rename
POST /races/<race_id>/routes/<route_id>/delete
POST /races/<race_id>/categories/add
POST /races/<race_id>/categories/<category_id>/edit
POST /races/<race_id>/categories/<category_id>/delete
POST /races/<race_id>/route/upload
POST /races/<race_id>/route/remove
GET  /races/<race_id>/route/geojson
POST /races/<race_id>/riders/add
POST /races/<race_id>/riders/<race_rider_id>/edit
POST /races/<race_id>/riders/<race_rider_id>/remove

The module retains Flask request/response glue. Race lifecycle, route, assignment,
timing, and track logic live in focused service modules; pure parsing lives in
src.utils.races. Placeholder routes remain web-only until their features exist.
"""

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from src.auth.decorators import (
    admin_required,
    require_rider_resource_access,
    rider_required,
    user_has_role,
)
from src.db.models import SessionLocal
from src.services.race_riders import (
    create_race_rider,
    delete_race_rider,
    get_scoped_race_rider,
    update_race_rider,
)
from src.services.race_entry import (
    RaceEntryValidationError,
    assign_device_and_create_entry,
    load_race_entry_page_data,
)
from src.services.race_routes import (
    RaceRouteNotFoundError,
    RaceRouteValidationError,
    clear_route_gpx,
    assign_race_category_route,
    create_race_category_with_route,
    create_race_route,
    delete_unused_race_category,
    delete_unused_race_route,
    get_category_for_race,
    get_route_geojson,
    rename_race_category,
    rename_race_route,
    reorder_race_category,
    set_race_category_archived,
    store_route_gpx,
)
from src.services.race_timing import (
    RaceRiderFinishMissingError,
    RaceRiderTimingNotFoundError,
    confirm_race_rider_finish,
    list_race_rider_timings,
    race_rider_timing_payload,
    update_manual_race_rider_times,
)
from src.services.race_tracks import get_race_rider_track_geojson
from src.services.races import (
    RaceNotFoundError,
    RaceValidationError,
    load_post_race_data,
    load_race_edit_data,
    save_race as save_race_record,
)
from src.utils.gpx import build_geojson_for_device
from src.utils.races import (
    normalize_race_form,
    parse_manual_time_epoch,
    parse_positive_id,
)
from src.utils.race_entry import normalize_race_entry_form

bp_races = Blueprint("races", __name__, url_prefix="/races")


def _post_race_map_bootstrap_config(race_id: int) -> dict:
    """
    Build browser-safe map endpoint/page configuration.

    Input Args:
      race_id: race id for the page being rendered.

    Output:
      Dictionary containing safe frontend endpoint and page wiring values.

    Notes:
      This remains web-layer glue because it uses request.path and url_for. It
      deliberately excludes the provider key and quota configuration.
    """
    return {
        "configStatusUrl": url_for("map_tile_quota.map_config_status"),
        "tileUsageUrl": url_for("map_tile_quota.map_tile_usage"),
        "raceId": race_id,
        "pagePath": request.path,
        "satelliteUnavailableMessage": (
            "Satellite view is currently unavailable for this account, "
            "please try again later."
        ),
    }


@bp_races.route("/new", methods=["GET"])
@admin_required
def new_race():
    """
    Render a blank admin race form.

    Output:
      Rendered race_form.html response with empty route/assignment state.
    """
    return render_template(
        "race_form.html",
        race=None,
        routes=[],
        category_records=[],
        categories=[],
        selected_category=None,
        route=None,
        riders=[],
        devices=[],
        race_riders=[],
        last_device_by_rider={},
        message=None,
        success=None,
    )


@bp_races.route("/<int:race_id>/post", methods=["GET"])
def post_race(race_id: int):
    """
    Render post-race route, rider-track, and timing data.

    Input Args:
      race_id: Race primary key from the route.

    Output:
      Rendered post_race.html response or HTTP 404 when the race is missing.
    """
    session = SessionLocal()
    try:
        try:
            category_id = parse_positive_id(request.args.get("category_id"))
        except ValueError:
            return Response("Invalid category selection.", status=400)
        try:
            page_data = load_post_race_data(
                session,
                race_id,
                category_id,
            )
        except RaceNotFoundError:
            return Response("Race not found.", status=404)
        except RaceValidationError as error:
            return Response(str(error), status=400)
        return render_template(
            "post_race.html",
            **page_data,
            public_map_config=_post_race_map_bootstrap_config(race_id),
        )
    finally:
        session.close()


@bp_races.route("/<int:race_id>/enter", methods=["GET", "POST"])
@rider_required
def enter_race(race_id: int):
    """
    Enter a rider in a category using automatic locked device assignment.

    Input Args:
      race_id: Race primary key from the route.

    Output:
      Rendered race_entry.html response or mapped 400/404/409/500 response.
    """
    if user_has_role(current_user, {"admin"}):
        return redirect(url_for("races.enter_race_admin", race_id=race_id))
    selected_rider_id = getattr(current_user, "rider_id", None)
    if selected_rider_id is None:
        return Response("Complete your rider profile before entering a race.", status=400)

    # Rider identity is derived exclusively from the authenticated account. No
    # submitted rider_id is read or trusted on this self-service endpoint.
    return _race_entry_response(
        race_id,
        selected_rider_id,
        include_rider_selector=False,
    )


@bp_races.route("/<int:race_id>/entries/new", methods=["GET", "POST"])
@admin_required
def enter_race_admin(race_id: int):
    """
    Enter a selected rider through the explicitly administrator-only workflow.

    Input Args:
      race_id: Race primary key from the route.

    Output:
      Rendered race_entry.html response or mapped validation response.

    Notes:
      Keeping admin-on-behalf selection on a separate protected endpoint makes
      the rider self-entry identity invariant unambiguous and auditable.
    """
    raw_rider_id = (
        request.form.get("rider_id")
        if request.method == "POST"
        else request.args.get("rider_id")
    )
    try:
        selected_rider_id = parse_positive_id(raw_rider_id)
    except ValueError:
        return Response("Invalid rider selection.", status=400)
    return _race_entry_response(
        race_id,
        selected_rider_id,
        include_rider_selector=True,
    )


def _race_entry_response(
    race_id: int,
    selected_rider_id: int | None,
    include_rider_selector: bool,
):
    """
    Render and process the shared staged rider/admin entry workflow.

    The first GET stage selects the rider when administered and then the
    Category id. The POST stage recalculates rider history, category scope,
    existing entry, and device availability before invoking the locked
    assignment service.
    """
    if request.method == "POST" and selected_rider_id is None:
        return Response("Rider selection is required.", status=400)
    raw_category_id = (
        request.form.get("category_id")
        if request.method == "POST"
        else request.args.get("category_id")
    )
    try:
        selected_category_id = parse_positive_id(raw_category_id)
    except ValueError:
        return Response("Invalid category selection.", status=400)

    session = SessionLocal()
    try:
        try:
            page_data = load_race_entry_page_data(
                session,
                race_id,
                selected_rider_id,
                include_rider_selector,
                selected_category_id,
            )
        except RaceEntryValidationError as error:
            status = 404 if "not found" in str(error).lower() else 400
            return Response(str(error), status=status)

        if request.method == "GET":
            return render_template(
                "race_entry.html",
                **page_data,
                result=None,
                message=None,
                success=None,
            )

        form, errors = normalize_race_entry_form(request.form)
        if errors:
            return render_template(
                "race_entry.html",
                **page_data,
                result=None,
                message=" ".join(errors),
                success=False,
            ), 400

        try:
            result = assign_device_and_create_entry(
                session,
                race_id,
                selected_rider_id,
                form["category_id"],
                form["has_device"],
                form["confirms_previous_device"],
            )
        except RaceEntryValidationError as error:
            return render_template(
                "race_entry.html",
                **page_data,
                result=None,
                message=str(error),
                success=False,
            ), 400

        if result.race_rider is None:
            session.rollback()
            return render_template(
                "race_entry.html",
                **page_data,
                result=result,
                message=result.message,
                success=False,
            ), 409

        session.commit()
        refreshed_page_data = load_race_entry_page_data(
            session,
            race_id,
            selected_rider_id,
            include_rider_selector,
            form["category_id"],
        )
        return render_template(
            "race_entry.html",
            **refreshed_page_data,
            result=result,
            message=result.message,
            success=True,
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/post-admin", methods=["GET"])
@admin_required
def post_race_admin(race_id: int):
    """
    Render the future admin post-race controls placeholder.

    Input Args:
      race_id: Race primary key from the route.

    Output:
      Rendered placeholder.html response.
    """
    return render_template(
        "placeholder.html",
        title="Admin Post Race",
        description="Future admin race tracking and timing-control page.",
        route=f"/races/{race_id}/post-admin",
        access="admin",
        back_url=url_for("home.dashboard_admin"),
        back_label="Back to Admin Dashboard",
    )


@bp_races.route("/<int:race_id>/results", methods=["GET"])
def race_results(race_id: int):
    """
    Render the future public official-results placeholder.

    Input Args:
      race_id: Race primary key from the route.

    Output:
      Rendered placeholder.html response.
    """
    return render_template(
        "placeholder.html",
        title="Official Race Results",
        description="Future official race results and rider GPX download page.",
        route=f"/races/{race_id}/results",
        access="all viewers",
        back_url=url_for("home.dashboard"),
        back_label="Back to Dashboard",
    )


@bp_races.route("/<int:race_id>/race-rider-timings", methods=["GET"])
def race_rider_timings(race_id: int):
    """
    Return live race-rider timing payloads for post-race polling.

    Input Args:
      race_id: Race primary key from the route.

    Output:
      JSON response scoped to the optional category query parameter.
    """
    try:
        category_id = parse_positive_id(request.args.get("category_id"))
    except ValueError:
        return jsonify({"error": "Invalid category selection."}), 400
    session = SessionLocal()
    try:
        riders = list_race_rider_timings(session, race_id, category_id)
        return jsonify(
            {
                "race_id": race_id,
                "category_id": category_id,
                "riders": riders,
            }
        ), 200
    except SQLAlchemyError as error:
        return jsonify({"error": f"DB error: {error}"}), 500
    finally:
        session.close()


@bp_races.route("/<int:race_id>/device/<device_id>/geojson", methods=["GET"])
def device_geojson(race_id: int, device_id: str):
    """
    Build and return unsaved GeoJSON for a device track.

    Input Args:
      race_id: Race primary key retained in the public URL contract.
      device_id: Device primary key whose points should be converted.

    Output:
      JSON response or HTTP 404 when no track can be built.
    """
    session = SessionLocal()
    try:
        ok, result = build_geojson_for_device(
            device_id=device_id,
            session=session,
            save=False,
        )
        if not ok:
            return Response(result, status=404)
        return Response(result, mimetype="application/json")
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/race-rider/<int:race_rider_id>/track",
    methods=["GET"],
)
def race_rider_track(race_id: int, race_rider_id: int):
    """
    Return stored history-first GeoJSON for one scoped race entry.

    Input Args:
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      JSON track response or HTTP 404 when neither history nor cache exists.
    """
    # Preserve the established history-first behavior. The service still accepts
    # prefer_cache for future live polling callers without duplicating query logic.
    prefer_cache = False
    session = SessionLocal()
    try:
        geojson = get_race_rider_track_geojson(
            session,
            race_id,
            race_rider_id,
            prefer_cache=prefer_cache,
        )
        if geojson:
            return Response(geojson, mimetype="application/json")
        return Response("Track not found for this race rider.", status=404)
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/race-rider/<int:race_rider_id>/manual-times",
    methods=["POST"],
)
@admin_required
def manual_times(race_id: int, race_rider_id: int):
    """
    Manually replace or clear RFID start/finish times.

    Input Args:
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      JSON success/error response. The service also stages a trimmed TrackHist
      snapshot when the latest raw tracker text contains fixes in the new window.
    """
    data = request.get_json(silent=True) or {}
    start_raw = (data.get("start_time") or "").strip()
    finish_raw = (data.get("end_time") or "").strip()
    try:
        start_epoch = parse_manual_time_epoch(start_raw)
    except ValueError:
        return jsonify({"error": "Invalid start_time format"}), 400
    try:
        finish_epoch = parse_manual_time_epoch(finish_raw)
    except ValueError:
        return jsonify({"error": "Invalid end_time format"}), 400

    session = SessionLocal()
    try:
        try:
            update_manual_race_rider_times(
                session,
                race_id,
                race_rider_id,
                start_epoch,
                finish_epoch,
            )
        except RaceRiderTimingNotFoundError:
            return jsonify({"error": "Race rider not found"}), 404
        session.commit()
        return jsonify({"ok": True}), 200
    except SQLAlchemyError as error:
        session.rollback()
        return jsonify({"error": f"DB error: {error}"}), 500
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/race-rider/<int:race_rider_id>/confirm-finish",
    methods=["POST"],
)
@admin_required
def confirm_finish_time(race_id: int, race_rider_id: int):
    """
    Confirm a race entry's current RFID finish time.

    Input Args:
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      JSON success/timing payload or mapped 400/404/500 error response.
    """
    session = SessionLocal()
    try:
        try:
            race_rider = confirm_race_rider_finish(
                session,
                race_id,
                race_rider_id,
            )
        except RaceRiderTimingNotFoundError:
            return jsonify({"error": "Race rider not found"}), 404
        except RaceRiderFinishMissingError:
            return jsonify({"error": "Cannot confirm a missing finish time"}), 400
        session.commit()
        return jsonify(
            {
                "ok": True,
                "timing": race_rider_timing_payload(race_rider),
            }
        ), 200
    except SQLAlchemyError as error:
        session.rollback()
        return jsonify({"error": f"DB error: {error}"}), 500
    finally:
        session.close()


@bp_races.route("/save", methods=["POST"])
@admin_required
def save_race():
    """
    Create or update a race from the admin form.

    Output:
      Redirect to the race edit page or mapped 400/404/500 response.
    """
    form = normalize_race_form(request.form)
    session = SessionLocal()
    try:
        try:
            race = save_race_record(session, form)
        except RaceValidationError as error:
            return Response(str(error), status=400)
        except RaceNotFoundError:
            return Response("Race not found.", status=404)
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race.id))
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/edit", methods=["GET"])
@admin_required
def edit_race(race_id: int):
    """
    Render category-scoped route and entry management for a race.

    Input Args:
      race_id: Race primary key.

    Output:
      Rendered race_form.html response or HTTP 404 when the race is missing.
    """
    session = SessionLocal()
    try:
        try:
            category_id = parse_positive_id(request.args.get("category_id"))
        except ValueError:
            return Response("Invalid category selection.", status=400)
        try:
            page_data = load_race_edit_data(
                session,
                race_id,
                category_id,
            )
        except RaceNotFoundError:
            return Response("Race not found.", status=404)
        except RaceValidationError as error:
            return Response(str(error), status=400)
        return render_template(
            "race_form.html",
            **page_data,
            message=None,
            success=None,
        )
    finally:
        session.close()


@bp_races.route("/<int:race_id>/routes/add", methods=["POST"])
@admin_required
def add_race_route(race_id: int):
    """
    Create one named route independently of category creation.

    Input Args:
      race_id: Race primary key.

    Output:
      Redirect to the race edit page or mapped 400/404/500 response.
    """
    session = SessionLocal()
    try:
        try:
            create_race_route(
                session,
                race_id,
                request.form.get("route_name") or "",
            )
        except RaceRouteValidationError as error:
            return Response(str(error), status=400)
        except RaceRouteNotFoundError:
            return Response("Race not found.", status=404)
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race_id))
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/routes/<int:route_id>/rename",
    methods=["POST"],
)
@admin_required
def rename_route(race_id: int, route_id: int):
    """
    Rename one route owned by the selected race.

    Input Args:
      race_id: Race primary key.
      route_id: Route primary key.

    Output:
      Redirect to race administration or mapped 400/500 response.
    """
    session = SessionLocal()
    try:
        try:
            rename_race_route(
                session,
                race_id,
                route_id,
                request.form.get("route_name") or "",
            )
        except RaceRouteValidationError as error:
            return Response(str(error), status=400)
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race_id))
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/routes/<int:route_id>/delete",
    methods=["POST"],
)
@admin_required
def delete_route(race_id: int, route_id: int):
    """
    Delete a race route only when no active or archived category uses it.

    Input Args:
      race_id: owning Race primary key.
      route_id: candidate Route primary key.

    Output:
      Redirect after deletion or mapped 404/409/500 response.
    """
    session = SessionLocal()
    try:
        try:
            delete_unused_race_route(session, race_id, route_id)
        except RaceRouteNotFoundError as error:
            return Response(str(error), status=404)
        except RaceRouteValidationError as error:
            return Response(str(error), status=409)
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race_id))
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/categories/add", methods=["POST"])
@admin_required
def add_race_category(race_id: int):
    """
    Create a category on an existing route or a newly named route.

    Input Args:
      race_id: Race primary key.

    Output:
      Redirect to the new category tab or mapped 400/404/500 response.
    """
    route_choice = (request.form.get("route_choice") or "").strip()
    route_id = None
    if route_choice and route_choice != "new":
        try:
            route_id = int(route_choice)
        except ValueError:
            return Response("Invalid route selection.", status=400)

    session = SessionLocal()
    try:
        try:
            _, category = create_race_category_with_route(
                session,
                race_id,
                request.form.get("category_name") or "",
                route_id=route_id,
                new_route_name=request.form.get("new_route_name"),
            )
        except RaceRouteValidationError as error:
            return Response(str(error), status=400)
        except RaceRouteNotFoundError:
            return Response("Race not found.", status=404)
        session.commit()
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=category.id,
            )
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/categories/<int:category_id>/edit",
    methods=["POST"],
)
@admin_required
def edit_race_category(race_id: int, category_id: int):
    """
    Rename, reorder, archive, and reassign one race category.

    Input Args:
      race_id: Race primary key.
      category_id: Category primary key.

    Output:
      Redirect to race administration or mapped 400/500 response.
    """
    try:
        display_order = int(request.form.get("display_order"))
        route_id = int(request.form.get("route_id"))
    except (TypeError, ValueError):
        return Response("Invalid category order or route selection.", status=400)

    session = SessionLocal()
    try:
        try:
            category = rename_race_category(
                session,
                race_id,
                category_id,
                request.form.get("category_name") or "",
            )
            reorder_race_category(
                session,
                race_id,
                category_id,
                display_order,
            )
            assign_race_category_route(
                session,
                race_id,
                category_id,
                route_id,
            )
            set_race_category_archived(
                session,
                race_id,
                category_id,
                request.form.get("archived") == "on",
            )
        except RaceRouteValidationError as error:
            return Response(str(error), status=400)
        session.commit()
        redirect_category_id = None if category.archived else category.id
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=redirect_category_id,
            )
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/categories/<int:category_id>/delete",
    methods=["POST"],
)
@admin_required
def delete_race_category(race_id: int, category_id: int):
    """
    Delete an unused category while requiring archive for historical rows.

    Input Args:
      race_id: owning Race primary key.
      category_id: candidate Category primary key.

    Output:
      Redirect after deletion or mapped 404/409/500 response.
    """
    session = SessionLocal()
    try:
        try:
            delete_unused_race_category(session, race_id, category_id)
        except RaceRouteNotFoundError as error:
            return Response(str(error), status=404)
        except RaceRouteValidationError as error:
            return Response(str(error), status=409)
        session.commit()
        return redirect(url_for("races.edit_race", race_id=race_id))
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/route/upload", methods=["POST"])
@admin_required
def upload_gpx(race_id: int):
    """
    Validate and store GPX/GeoJSON for the selected race category.

    Input Args:
      race_id: Race primary key.

    Output:
      Redirect to the race edit tab or mapped 400/500 response.
    """
    try:
        category_id = parse_positive_id(
            request.form.get("category_id"),
            required=True,
        )
    except ValueError:
        return Response("Invalid category selection.", status=400)
    uploaded_file = request.files.get("gpx_file")
    if not uploaded_file or uploaded_file.filename == "":
        return Response("Please choose a GPX file.", status=400)
    gpx_text = uploaded_file.read().decode("utf-8", errors="ignore")

    session = SessionLocal()
    try:
        try:
            store_route_gpx(
                session,
                race_id,
                category_id,
                gpx_text,
            )
        except RaceRouteValidationError as error:
            return Response(str(error), status=400)
        session.commit()
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=category_id,
            )
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/route/remove", methods=["POST"])
@admin_required
def remove_gpx(race_id: int):
    """
    Clear GPX/GeoJSON for the selected category without deleting its route.

    Input Args:
      race_id: Race primary key.

    Output:
      Redirect to the edit tab or mapped 404/500 response.
    """
    try:
        category_id = parse_positive_id(
            request.form.get("category_id"),
            required=True,
        )
    except ValueError:
        return Response("Invalid category selection.", status=400)
    session = SessionLocal()
    try:
        try:
            clear_route_gpx(session, race_id, category_id)
        except RaceRouteNotFoundError:
            return Response("No route for that category.", status=404)
        session.commit()
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=category_id,
            )
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route("/<int:race_id>/route/geojson", methods=["GET"])
def route_geojson(race_id: int):
    """
    Return stored route GeoJSON for the selected category.

    Input Args:
      race_id: Race primary key.

    Output:
      Stored JSON response or an empty FeatureCollection.
    """
    try:
        category_id = parse_positive_id(
            request.args.get("category_id"),
            required=True,
        )
    except ValueError:
        return Response("Invalid category selection.", status=400)
    session = SessionLocal()
    try:
        geojson = get_route_geojson(session, race_id, category_id)
        if not geojson:
            return jsonify({"type": "FeatureCollection", "features": []})
        return Response(geojson, mimetype="application/json")
    finally:
        session.close()


@bp_races.route("/<int:race_id>/riders/add", methods=["POST"])
@admin_required
def add_race_rider(race_id: int):
    """
    Add a rider/device assignment to the selected race category.

    Input Args:
      race_id: Race primary key.

    Output:
      Redirect to the edit tab or mapped 400/500 response.
    """
    try:
        rider_id = parse_positive_id(request.form.get("rider_id"), required=True)
        category_id = parse_positive_id(
            request.form.get("category_id"),
            required=True,
        )
    except ValueError:
        return Response("Invalid rider or category selection.", status=400)
    device_id = (request.form.get("device_id") or "").strip()

    session = SessionLocal()
    try:
        try:
            category = get_category_for_race(session, race_id, category_id)
            if category is None:
                return Response("Invalid category.", status=400)
            create_race_rider(
                session,
                race_id,
                rider_id,
                device_id,
                category.id,
            )
            session.commit()
        except SQLAlchemyError as error:
            session.rollback()
            return Response(f"DB error: {error}", status=500)
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=category.id,
            )
        )
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/riders/<int:race_rider_id>/edit",
    methods=["POST"],
)
@rider_required
def edit_race_rider(race_id: int, race_rider_id: int):
    """
    Update one scoped rider assignment's device and flags.

    Input Args:
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      Redirect to the edit tab or mapped 404/500 response.
    """
    device_id = (request.form.get("device_id") or "").strip()
    active = request.form.get("active") == "on"
    recording = request.form.get("recording") == "on"

    session = SessionLocal()
    try:
        race_rider = get_scoped_race_rider(session, race_id, race_rider_id)
        if race_rider is None:
            return Response("RaceRider not found.", status=404)
        require_rider_resource_access(current_user, race_rider.rider_id)
        category_id = race_rider.category_id
        update_race_rider(race_rider, device_id, active, recording)
        session.commit()
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=category_id,
            )
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()


@bp_races.route(
    "/<int:race_id>/riders/<int:race_rider_id>/remove",
    methods=["POST"],
)
@rider_required
def remove_race_rider(race_id: int, race_rider_id: int):
    """
    Remove one scoped rider assignment.

    Input Args:
      race_id: Race primary key.
      race_rider_id: RaceRider primary key.

    Output:
      Redirect to the edit tab or mapped 404/500 response.
    """
    session = SessionLocal()
    try:
        race_rider = get_scoped_race_rider(session, race_id, race_rider_id)
        if race_rider is None:
            return Response("RaceRider not found.", status=404)
        require_rider_resource_access(current_user, race_rider.rider_id)
        category_id = race_rider.category_id
        delete_race_rider(session, race_rider)
        session.commit()
        return redirect(
            url_for(
                "races.edit_race",
                race_id=race_id,
                category_id=category_id,
            )
        )
    except SQLAlchemyError as error:
        session.rollback()
        return Response(f"DB error: {error}", status=500)
    finally:
        session.close()
