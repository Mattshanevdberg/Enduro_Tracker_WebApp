"""
Riders form: create a new rider and save to the 'riders' table.

Notes
-----
- Validates minimal required fields.
- Category is constrained in code to three allowed values.
- On success, shows a confirmation message on the same page.
- Now also lists existing riders and supports editing them on the same form.
"""

from flask import Blueprint, request, render_template, abort
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional

from src.db.models import SessionLocal, Rider

bp_riders = Blueprint("riders", __name__, url_prefix="/riders")

CATEGORIES = ["Professional", "Open", "Junior"]

def _validate_category(value: str) -> bool:
    # returns true if value is in categories
    return value in CATEGORIES

@bp_riders.route("/new", methods=["GET", "POST"])
@bp_riders.route("/<int:rider_id>/edit", methods=["GET", "POST"])
def rider_form(rider_id: Optional[int] = None):
    """
    Create or edit riders. GET renders the form and table of riders.
    POST saves either a new rider or updates an existing one when rider_id is present.
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

        if request.method == "GET":
            rider = session.query(Rider).get(rider_id) if rider_id else None
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
