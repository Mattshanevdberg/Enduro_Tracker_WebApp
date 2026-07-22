"""
Public rider-profile index redirect and read-only profile controllers.

Routes
------
GET /rider
    Redirect the retired standalone rider index to the dashboard Riders tab.
GET /rider/<rider_id>
    Render one public rider profile for direct navigation and dashboard popups.
GET /rider/<rider_id>/profile-image
    Serve the Rider's normalized public image from persistent media storage.

Rider lookup remains in src.services.riders. This web module owns redirects,
404 behavior, edit-link visibility, canonical-page rendering, and responses.
"""

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    send_from_directory,
    url_for,
)
from flask_login import current_user

from src.auth.decorators import user_can_access_rider_resource
from src.db.models import SessionLocal
from src.services.profile_images import is_profile_image_key
from src.services.riders import get_rider


bp_rider_profiles = Blueprint("rider_profiles", __name__)


@bp_rider_profiles.route("/rider", methods=["GET"])
def rider_profiles():
    """
    Redirect the former rider index to the dashboard Riders tab.

    Output:
      Redirect response selecting the public dashboard rider collection.
    """
    return redirect(url_for("home.dashboard", tab="riders"))


@bp_rider_profiles.route("/rider/<int:rider_id>", methods=["GET"])
def rider_profile(rider_id: int):
    """
    Render a public, read-only rider profile.

    Input Args:
      rider_id: Rider primary key from the public route.

    Output:
      Rendered rider_profile.html response, or HTTP 404 for a missing rider.

    Notes:
      Dashboard JavaScript reads the marked profile region into an accessible
      dialog. Without JavaScript, the same link remains a complete standalone
      page. Owners and admins receive the existing authorized edit-page link.
    """
    session = SessionLocal()
    try:
        rider = get_rider(session, rider_id)
        if rider is None:
            abort(404)
        return render_template(
            "rider_profile.html",
            rider=rider,
            can_edit=user_can_access_rider_resource(current_user, rider.id),
        )
    finally:
        session.close()


@bp_rider_profiles.route(
    "/rider/<int:rider_id>/profile-image",
    methods=["GET"],
)
def rider_profile_image(rider_id: int):
    """
    Serve one public normalized Rider profile image.

    Input Args:
      rider_id: Rider primary key from the public media URL.

    Output:
      Conditional image/webp response, or HTTP 404 when the Rider, generated
      key, or underlying persistent file is missing.

    Security:
      The database value must match the application-generated flat key pattern
      and embed the requested Rider id. send_from_directory supplies an
      additional safe path boundary. Uploaded SVG/script-capable formats are
      never stored by the processing pipeline.
    """
    session = SessionLocal()
    try:
        rider = get_rider(session, rider_id)
        if rider is None or not is_profile_image_key(
            rider.profile_image_filename,
            rider_id=rider.id,
        ):
            abort(404)
        response = send_from_directory(
            current_app.config["PROFILE_IMAGE_UPLOAD_DIR"],
            rider.profile_image_filename,
            mimetype="image/webp",
            conditional=True,
            max_age=31536000,
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    finally:
        session.close()
