"""
Landing and dashboard routes.

The public landing page is separate from the operational dashboards. Viewers can
reach the public race dashboard without logging in, while the admin dashboard
keeps the management controls that used to live on the home page.
"""

from flask import Blueprint, render_template

from src.auth.decorators import admin_required
from src.db.models import SessionLocal, Race, config as app_config
from src.utils.time import epoch_to_datetime

bp_home = Blueprint("home", __name__)


def _race_display_data(active_only: bool = False):
    """
    Load races and add display-friendly datetime fields.

    Input Args:
      active_only: when True, return only active races.

    Output:
      Tuple of race list and default configured category.
    """
    session = SessionLocal()
    try:
        query = session.query(Race)
        if active_only:
            query = query.filter(Race.active.is_(True))
        races = query.order_by(Race.starts_at_epoch.asc()).all()
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
    return races, default_category


@bp_home.route("/")
def home_page():
    """
    Render the public landing page.
    """
    return render_template("landing.html")


@bp_home.route("/dashboard")
def dashboard():
    """
    Render the public dashboard with active races only.
    """
    races, default_category = _race_display_data(active_only=True)
    return render_template(
        "dashboard.html",
        races=races,
        default_category=default_category,
    )


@bp_home.route("/dashboard-admin")
@admin_required
def dashboard_admin():
    """
    Render the admin operational dashboard.
    """
    races, default_category = _race_display_data(active_only=False)
    return render_template(
        "dashboard_admin.html",
        races=races,
        default_category=default_category,
    )
