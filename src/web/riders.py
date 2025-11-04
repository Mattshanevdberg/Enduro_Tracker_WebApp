"""
Riders form: create a new rider and save to the 'riders' table.

Notes
-----
- Validates minimal required fields.
- Category is constrained in code to three allowed values.
- On success, shows a confirmation message on the same page.
"""

from flask import Blueprint, request, render_template
from sqlalchemy.exc import SQLAlchemyError

from src.db.models import SessionLocal, Rider

bp_riders = Blueprint("riders", __name__, url_prefix="/riders")

CATEGORIES = ["Professional", "Open", "Junior"]

def _validate_category(value: str) -> bool:
    # returns true if value is in catagories
    return value in CATEGORIES

@bp_riders.route("/new", methods=["GET", "POST"])
def rider_form():
    """
    Create a new rider via simple HTML form.

    GET  -> render form
    POST -> validate, insert, and show a success message
    """
    if request.method == "GET":
        return render_template(
            "riders_form.html",
            categories=CATEGORIES,
            message=None,
            success=None,
            form={"category": ""}
        )

    # POST: read inputs
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
        return render_template(
            "riders_form.html",
            categories=CATEGORIES,
            message=" ".join(errors),
            success=False,
            form={"category": category}
        ), 400

    # insert
    session = SessionLocal()
    try:
        r = Rider(
            name=full_name,
            category=category,
            team=team,
            bike=bike,
            bio=bio
        )
        session.add(r)
        session.commit()
        msg = f"Saved rider: {full_name} ({category})."
        return render_template(
            "riders_form.html",
            categories=CATEGORIES,
            message=msg,
            success=True,
            form={"category": category}
        )
    except SQLAlchemyError as e:
        session.rollback()
        return render_template(
            "riders_form.html",
            categories=CATEGORIES,
            message=f"DB error: {e}",
            success=False,
            form={"category": category}
        ), 500
    finally:
        session.close()
