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
"""

from typing import Optional

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError

from src.auth.decorators import rider_required, user_can_access_rider_resource
from src.db.models import SessionLocal
from src.services.riders import (
    RiderProfileLinkError,
    RiderValidationError,
    create_rider,
    get_rider,
    list_riders,
    rider_account_has_profile,
    update_rider,
)
from src.utils.riders import (
    normalize_rider_form,
    rider_form_values,
)

bp_riders = Blueprint("riders", __name__, url_prefix="/riders")

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
      profile, or HTTP 400/403/404/500 according to the request outcome.

    Access:
      Active admins may create/edit any profile. Active riders may create one
      linked profile and edit only their own linked profile.
    """
    session = SessionLocal()
    form = rider_form_values()
    editing_rider = None
    riders = []

    try:
        riders = list_riders(session)

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

            return render_template(
                "riders_form.html",
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
            editing_rider = get_rider(session, submitted_rider_id)
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

        try:
            if editing_rider is not None:
                update_rider(editing_rider, form)
                message = f"Updated rider: {form['name']}"
            else:
                create_rider(session, form, current_user)
                message = f"Saved rider: {form['name']}."
            session.commit()
        except RiderValidationError as error:
            return render_template(
                "riders_form.html",
                message=str(error),
                success=False,
                form=rider_form_values(form),
                riders=riders,
                editing_rider=editing_rider,
            ), 400
        except RiderProfileLinkError:
            session.rollback()
            abort(403)

        return render_template(
            "riders_form.html",
            message=message,
            success=True,
            form=rider_form_values(),
            riders=list_riders(session),
            editing_rider=None,
        )
    except SQLAlchemyError as error:
        session.rollback()
        return render_template(
            "riders_form.html",
            message=f"DB error: {error}",
            success=False,
            form=rider_form_values(form),
            riders=riders,
            editing_rider=editing_rider,
        ), 500
    finally:
        session.close()
