"""
Rider/admin HTTP controller for rider profile creation and editing.

Routes
------
GET/POST /riders/new
    Render the rider form or create a new rider profile.
GET/POST /riders/<rider_id>/edit
    Render or update an existing rider profile.

Notes
-----
* Active rider/admin access is enforced by rider_required.
* Shared rider-resource authorization protects profile edits.
* Pure form handling lives in src.utils.riders.
* Rider queries, mutations, and account-link rules live in src.services.riders.
* Generated profile-image files are coordinated by src.services.profile_images.
"""

from typing import Optional

from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from src.auth.decorators import rider_required, user_can_access_rider_resource
from src.db.models import SessionLocal
from src.services.profile_images import (
    ProfileImageStorageError,
    delete_profile_image,
    store_profile_image,
)
from src.services.riders import (
    RiderProfileLinkError,
    RiderValidationError,
    create_rider,
    get_rider,
    get_rider_for_update,
    list_riders,
    rider_account_has_profile,
    update_rider,
)
from src.utils.profile_images import ProfileImageValidationError
from src.utils.riders import (
    normalize_rider_form,
    rider_form_values,
)

bp_riders = Blueprint("riders", __name__, url_prefix="/riders")
PROFILE_IMAGE_MULTIPART_ALLOWANCE_BYTES = 128 * 1024


def _profile_image_settings() -> tuple[str, int]:
    """
    Read request-scoped profile-image storage configuration.

    Output:
      Tuple containing the persistent upload directory and maximum byte count.
    """
    return (
        current_app.config["PROFILE_IMAGE_UPLOAD_DIR"],
        int(current_app.config["PROFILE_IMAGE_MAX_BYTES"]),
    )


def _render_rider_form(
    *,
    message,
    success,
    form,
    riders,
    editing_rider,
    status_code: int = 200,
):
    """
    Render the rider form with shared upload and authorization context.

    Input Args:
      message: optional user-facing outcome or validation message.
      success: True, False, or None for message presentation.
      form: template-safe rider form values.
      riders: visible Rider rows for the existing-riders table.
      editing_rider: Rider row currently being edited, or None for creation.
      status_code: HTTP status returned with the rendered page.

    Output:
      Rendered riders_form.html response, optionally paired with a non-200
      status code.
    """
    _, max_bytes = _profile_image_settings()
    response = render_template(
        "riders_form.html",
        message=message,
        success=success,
        form=form,
        riders=riders,
        editing_rider=editing_rider,
        viewer=current_user,
        profile_image_max_mb=max_bytes / (1024 * 1024),
    )
    return response if status_code == 200 else (response, status_code)


def _delete_obsolete_profile_image(key: str | None, rider_id: int) -> None:
    """
    Best-effort delete a superseded generated image after database commit.

    Input Args:
      key: previous Rider.profile_image_filename value.
      rider_id: owning Rider primary key embedded in generated keys.

    Output:
      None. A failed cleanup is logged without reversing a committed profile
      update; the unreferenced file can be removed during maintenance.
    """
    upload_directory, _ = _profile_image_settings()
    try:
        delete_profile_image(upload_directory, key, rider_id=rider_id)
    except ProfileImageStorageError as error:
        current_app.logger.warning("Profile-image cleanup failed: %s", error)


@bp_riders.route("/new", methods=["GET", "POST"])
@bp_riders.route("/<int:rider_id>/edit", methods=["GET", "POST"])
@rider_required
def rider_form(rider_id: Optional[int] = None):
    """
    Render, create, or update a rider profile.

    Input Args:
      rider_id: optional Rider primary key from the edit route.

    Output:
      Rendered riders_form.html response, redirect to an existing rider-owned
      profile, or HTTP 400/403/404/413/500 according to the request outcome.

    Access:
      Active admins may create/edit any profile. Active riders may create one
      linked profile and edit only their own linked profile.
    """
    session = SessionLocal()
    form = rider_form_values()
    editing_rider = None
    riders = []
    new_profile_image_key = None

    try:
        riders = list_riders(session)

        # Reject clearly oversized multipart bodies before Flask/Werkzeug parses
        # request.files. A small allowance covers field names and text fields;
        # the image utility still enforces the exact per-file byte limit.
        _, max_profile_image_bytes = _profile_image_settings()
        if (
            request.method == "POST"
            and request.content_length is not None
            and request.content_length
            > max_profile_image_bytes + PROFILE_IMAGE_MULTIPART_ALLOWANCE_BYTES
        ):
            abort(
                413,
                description=(
                    "The rider form is too large. Choose a profile picture "
                    f"no larger than {max_profile_image_bytes / (1024 * 1024):g} MB."
                ),
            )

        # A rider account owns at most one profile. GET sends that rider to the
        # existing edit page; POST is rejected so a forged request cannot create
        # a second unlinked Rider row.
        if rider_id is None and rider_account_has_profile(current_user):
            if request.method == "GET":
                return redirect(
                    url_for("riders.rider_form", rider_id=current_user.rider_id)
                )
            abort(403)

        if request.method == "GET":
            editing_rider = get_rider(session, rider_id) if rider_id else None
            if rider_id and editing_rider is None:
                abort(404)
            if rider_id and not user_can_access_rider_resource(current_user, rider_id):
                abort(403)

            return _render_rider_form(
                message=None,
                success=None,
                form=rider_form_values(editing_rider),
                riders=riders,
                editing_rider=editing_rider,
            )

        # The hidden edit id keeps the existing form contract. Access is checked
        # again against the resolved profile before any mutation is attempted.
        submitted_rider_id = request.form.get("rider_id") or rider_id
        if submitted_rider_id:
            try:
                submitted_rider_id = int(submitted_rider_id)
            except (TypeError, ValueError):
                abort(404)
            editing_rider = get_rider_for_update(session, submitted_rider_id)
            if editing_rider is None:
                abort(404)
            if not user_can_access_rider_resource(current_user, submitted_rider_id):
                abort(403)

        form = normalize_rider_form(
            request.form.get("name"),
            request.form.get("team"),
            request.form.get("bike"),
            request.form.get("bio"),
        )
        # The key is server-owned and never accepted from request.form. Keeping
        # the current value only supports preview rendering after text or image
        # validation fails.
        form["profile_image_filename"] = (
            editing_rider.profile_image_filename if editing_rider else None
        )
        uploaded_image = request.files.get("profile_image")
        has_uploaded_image = bool(
            uploaded_image is not None and (uploaded_image.filename or "").strip()
        )
        remove_profile_image = request.form.get("remove_profile_image") == "1"
        if has_uploaded_image and remove_profile_image:
            return _render_rider_form(
                message="Choose a new profile picture or remove the current one, not both.",
                success=False,
                form=rider_form_values(form),
                riders=riders,
                editing_rider=editing_rider,
                status_code=400,
            )

        try:
            if editing_rider is not None:
                update_rider(editing_rider, form)
                saved_rider = editing_rider
                message = f"Updated rider: {form['name']}"
            else:
                saved_rider = create_rider(session, form, current_user)
                message = f"Saved rider: {form['name']}."

            previous_profile_image_key = saved_rider.profile_image_filename
            if has_uploaded_image:
                upload_directory, max_bytes = _profile_image_settings()
                new_profile_image_key = store_profile_image(
                    uploaded_image.stream,
                    uploaded_image.filename,
                    upload_directory,
                    max_bytes,
                    saved_rider.id,
                )
                saved_rider.profile_image_filename = new_profile_image_key
            elif remove_profile_image:
                saved_rider.profile_image_filename = None
            saved_rider_id = saved_rider.id
            final_profile_image_key = saved_rider.profile_image_filename
            session.commit()
            # From this point onward the new key is a committed database value,
            # so a later rendering/query failure must not delete its file.
            new_profile_image_key = None
        except RiderValidationError as error:
            session.rollback()
            return _render_rider_form(
                message=str(error),
                success=False,
                form=rider_form_values(form),
                riders=riders,
                editing_rider=editing_rider,
                status_code=400,
            )
        except ProfileImageValidationError as error:
            session.rollback()
            form["profile_image_filename"] = previous_profile_image_key
            return _render_rider_form(
                message=str(error),
                success=False,
                form=rider_form_values(form),
                riders=riders,
                editing_rider=editing_rider,
                status_code=400,
            )
        except ProfileImageStorageError as error:
            session.rollback()
            return _render_rider_form(
                message=str(error),
                success=False,
                form=rider_form_values(form),
                riders=riders,
                editing_rider=editing_rider,
                status_code=500,
            )
        except RiderProfileLinkError:
            session.rollback()
            abort(403)

        if previous_profile_image_key != final_profile_image_key:
            _delete_obsolete_profile_image(previous_profile_image_key, saved_rider_id)

        return _render_rider_form(
            message=message,
            success=True,
            form=rider_form_values(),
            riders=list_riders(session),
            editing_rider=None,
        )
    except SQLAlchemyError as error:
        session.rollback()
        if new_profile_image_key is not None:
            upload_directory, _ = _profile_image_settings()
            try:
                delete_profile_image(upload_directory, new_profile_image_key)
            except ProfileImageStorageError:
                current_app.logger.exception(
                    "Could not clean an uncommitted profile-image upload."
                )
        return _render_rider_form(
            message=f"DB error: {error}",
            success=False,
            form=rider_form_values(form),
            riders=riders,
            editing_rider=editing_rider,
            status_code=500,
        )
    finally:
        session.close()
