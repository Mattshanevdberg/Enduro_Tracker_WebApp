"""
Landing and dashboard HTTP controllers.

Routes
------
GET /
    Render the public landing page.
GET /dashboard
    Render the public dashboard with active races.
GET /dashboard-admin
    Render the admin operational dashboard with all races.

Dashboard race queries and display preparation live in src.services.home. This
module retains only Flask access control, session boundaries, and rendering.
"""

from flask import Blueprint, render_template

from src.auth.decorators import admin_required
from src.db.models import SessionLocal
from src.services.home import load_race_display_data
from src.utils.riders import DEFAULT_RIDER_CATEGORIES

bp_home = Blueprint("home", __name__)


def _render_dashboard(template_name: str, active_only: bool):
    """
    Render a dashboard using service-prepared race display data.

    Input Args:
      template_name: dashboard template selected by the route.
      active_only: whether the service should return only active races.

    Output:
      Rendered Flask dashboard response.

    Notes:
      This remains in the web layer because it owns Flask template rendering and
      the request-scoped SQLAlchemy session boundary shared by both dashboards.
    """
    session = SessionLocal()
    try:
        races, default_category = load_race_display_data(
            session,
            active_only=active_only,
            categories=DEFAULT_RIDER_CATEGORIES,
        )
        return render_template(
            template_name,
            races=races,
            default_category=default_category,
        )
    finally:
        session.close()


@bp_home.route("/")
def home_page():
    """
    Render the public landing page.

    Output:
      Rendered landing.html response.
    """
    return render_template("landing.html")


@bp_home.route("/dashboard")
def dashboard():
    """
    Render the public dashboard with active races only.

    Output:
      Rendered dashboard.html response.
    """
    return _render_dashboard("dashboard.html", active_only=True)


@bp_home.route("/dashboard-admin")
@admin_required
def dashboard_admin():
    """
    Render the admin operational dashboard with all races.

    Output:
      Rendered dashboard_admin.html response.
    """
    return _render_dashboard("dashboard_admin.html", active_only=False)
