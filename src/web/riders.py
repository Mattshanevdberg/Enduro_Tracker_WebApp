"""
Riders form: create a new rider and save to the 'riders' table.

Notes
-----
- Validates minimal required fields.
- Category is constrained in code to three allowed values.
- On success, shows a confirmation message on the same page.
- Now also lists existing riders and supports editing them on the same form.
- Rider/admin login is required because the page creates or edits rider profile
  details.
- Admins can create and edit any rider profile.
- Riders can create only one linked rider profile and can edit only their own
  linked profile.

Paths
-----
GET/POST /riders/new              -> rider/admin create rider form
GET/POST /riders/<rider_id>/edit  -> rider/admin edit rider form
"""

from flask import Blueprint, request, render_template, abort, redirect, url_for
from flask_login import current_user
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional

from src.auth.decorators import rider_required, user_can_access_rider_resource
from src.db.models import SessionLocal, Rider, User
from src.utils.time import utc_now

bp_riders = Blueprint("riders", __name__, url_prefix="/riders")

CATEGORIES = ["Professional", "Open", "Junior"]


def _validate_category(value: str) -> bool:
    """
    Validate that a submitted category is supported.

    Input Args:
      value: raw category string from the form.

    Output:
      True when the category is one of the configured form options.
    """
    return value in CATEGORIES


def _is_rider_user(user) -> bool:
    """
    Check whether the current account is a rider account.

    Input Args:
      user: Flask-Login current_user or compatible User object.

    Output:
      True for rider users; otherwise False.
    """
    return getattr(user, "role", None) == "rider"


def _rider_already_exists(user) -> bool:
    """
    Check whether a rider user already has a linked Rider profile.

    Input Args:
      user: Flask-Login current_user or compatible User object.

    Output:
      True when the logged-in rider account already has user.rider_id set.

    Notes:
      This is an application-level guard that prevents normal riders from using
      /riders/new to create multiple Rider rows. The database uniqueness
      constraint on users.rider_id prevents two users from linking to the same
      Rider row, but this helper controls how many Rider rows one rider account
      can create through the browser.
    """
    return _is_rider_user(user) and getattr(user, "rider_id", None) is not None


def _can_edit_rider(user, rider_id: int) -> bool:
    """
    Check whether a user may edit a specific Rider row.

    Input Args:
      user: Flask-Login current_user or compatible User object.
      rider_id: Rider primary key being edited.

    Output:
      True when the user is an admin or when the user is a rider editing their
      own linked Rider row.
    """
    return user_can_access_rider_resource(user, rider_id)


@bp_riders.route("/new", methods=["GET", "POST"])
@bp_riders.route("/<int:rider_id>/edit", methods=["GET", "POST"])
@rider_required
def rider_form(rider_id: Optional[int] = None):
    """
    Create or edit riders. GET renders the form and table of riders.
    POST saves either a new rider or updates an existing one when rider_id is present.

    Access:
      Requires an active rider or admin account.

    Notes:
      Admins can create and edit any Rider row. Riders can create one linked
      Rider row when they do not already have one, and can edit only the Rider
      row referenced by current_user.rider_id.
    """
    session = SessionLocal()
    try:
        # Always have the latest list for the table
        riders = session.query(Rider).order_by(Rider.name.asc()).all()

        # Helper to build a form dict for template consumption
        def _form_from_obj(obj: Optional[Rider]):
            return {
                "name": obj.name if obj else "",
                "category": obj.category if obj else "",
                "team": obj.team if obj else "",
                "bike": obj.bike if obj else "",
                "bio": obj.bio if obj else "",
            }

        if rider_id is None and _rider_already_exists(current_user):
            if request.method == "GET":
                return redirect(url_for("riders.rider_form", rider_id=current_user.rider_id))
            abort(403)

        if request.method == "GET":
            rider = session.query(Rider).get(rider_id) if rider_id else None
            if rider_id and not rider:
                abort(404)
            if rider_id and not _can_edit_rider(current_user, rider_id):
                abort(403)

            form = _form_from_obj(rider)
            return render_template(
                "riders_form.html",
                categories=CATEGORIES,
                message=None,
                success=None,
                form=form,
                riders=riders,
                editing_rider=rider,
            )

        # POST -> create or update
        # Decide which rider we are editing (hidden input wins over URL for convenience)
        rid_val = request.form.get("rider_id") or rider_id
        editing_rider = session.query(Rider).get(int(rid_val)) if rid_val else None
        if rid_val and not editing_rider:
            abort(404)
        if rid_val and not _can_edit_rider(current_user, int(rid_val)):
            abort(403)

        full_name = (request.form.get("name") or "").strip()
        category = (request.form.get("category") or "").strip()
        team = (request.form.get("team") or "").strip() or None
        bike = (request.form.get("bike") or "").strip() or None
        bio = (request.form.get("bio") or "").strip() or None

        # basic validation
        errors = []
        if not full_name:
            errors.append("Name is required.")
        if not category or not _validate_category(category):
            errors.append("Category must be one of: Professional, Open, Junior.")

        if errors:
            form = {
                "name": full_name,
                "category": category,
                "team": team or "",
                "bike": bike or "",
                "bio": bio or "",
            }
            return render_template(
                "riders_form.html",
                categories=CATEGORIES,
                message=" ".join(errors),
                success=False,
                form=form,
                riders=riders,
                editing_rider=editing_rider,
            ), 400

        try:
            if editing_rider:
                editing_rider.name = full_name
                editing_rider.category = category
                editing_rider.team = team
                editing_rider.bike = bike
                editing_rider.bio = bio
                msg = f"Updated rider: {full_name}"
            else:
                new_rider = Rider(
                    name=full_name,
                    category=category,
                    team=team,
                    bike=bike,
                    bio=bio,
                )
                session.add(new_rider)
                session.flush()
                editing_rider = new_rider

                if _is_rider_user(current_user):
                    user = session.get(User, current_user.id)
                    if not user or getattr(user, "rider_id", None) is not None:
                        abort(403)
                    user.rider_id = new_rider.id
                    user.updated_at = utc_now()

                msg = f"Saved rider: {full_name} ({category})."

            session.commit()

            # refresh list to include changes; clear form so the user can add a new one
            riders = session.query(Rider).order_by(Rider.name.asc()).all()
            form = _form_from_obj(None)
            editing_rider = None

            return render_template(
                "riders_form.html",
                categories=CATEGORIES,
                message=msg,
                success=True,
                form=form,
                riders=riders,
                editing_rider=editing_rider,
            )
        except SQLAlchemyError as e:
            session.rollback()
            form = {
                "name": full_name,
                "category": category,
                "team": team or "",
                "bike": bike or "",
                "bio": bio or "",
            }
            return render_template(
                "riders_form.html",
                categories=CATEGORIES,
                message=f"DB error: {e}",
                success=False,
                form=form,
                riders=riders,
                editing_rider=editing_rider,
            ), 500
    finally:
        session.close()
