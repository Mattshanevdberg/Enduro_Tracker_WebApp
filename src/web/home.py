"""
Home page routes (simple navigation hub).
"""

from flask import Blueprint, render_template

from src.db.models import SessionLocal, Race, config as app_config
from src.utils.time import epoch_to_datetime

bp_home = Blueprint("home", __name__)

@bp_home.route("/")
def home_page():
    """
    Render the home page with navigation and a quick races table.
    """
    session = SessionLocal()
    try:
        races = session.query(Race).order_by(Race.starts_at_epoch.asc()).all()
    finally:
        session.close()

    # Convert epochs to datetimes for display in the template.
    for race in races:
        if race.starts_at_epoch is not None:
            race.starts_at = epoch_to_datetime(race.starts_at_epoch)
        else:
            race.starts_at = None

    categories = app_config.get("categories") or ["Professional", "Open", "Junior"]
    default_category = categories[0] if categories else ""

    return render_template(
        "home.html",
        races=races,
        default_category=default_category,
    )
