"""
Landing, search-crawler, sitemap, and dashboard HTTP controllers.

Routes
------
GET /
    Render the public landing page.
GET /robots.txt
    Return production crawler guidance and the canonical sitemap location.
GET /sitemap.xml
    Return the canonical public pages intended for search indexing.
GET /dashboard
    Render the public dashboard with active races.
GET /dashboard-admin
    Render the admin operational dashboard with all races.

Dashboard race queries and display preparation live in src.services.home. This
module retains only Flask access control, session boundaries, and rendering.
"""

from flask import Blueprint, Response, render_template

from src.auth.decorators import admin_required
from src.db.models import SessionLocal
from src.services.home import load_race_display_data

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
        races = load_race_display_data(
            session,
            active_only=active_only,
        )
        return render_template(
            template_name,
            races=races,
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


@bp_home.route("/robots.txt")
def robots_txt():
    """
    Tell search engines which public pages they may visit.

    Output:
      Plain-text crawler guidance that allows public viewer pages, excludes
      authenticated administration and management paths, and advertises the
      production sitemap location on every application hostname.

    Notes:
      This response guides cooperative search-engine crawlers only. It is not
      an access-control mechanism, so private routes must remain protected by
      their existing login and role decorators.
    """
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /api/v1/\n"
        "Disallow: /dashboard-admin\n"
        "Disallow: /devices\n"
        "Disallow: /rfid\n"
        "Disallow: /riders/\n"
        "Disallow: /races/new\n"
        "Disallow: /races/save\n"
        "Disallow: /races/*/edit\n"
        "Disallow: /races/*/enter\n"
        "Disallow: /races/*/entries/\n"
        "Disallow: /races/*/post-admin\n"
        "Disallow: /races/*/routes/\n"
        "Disallow: /races/*/categories/\n"
        "Disallow: /races/*/route/upload\n"
        "Disallow: /races/*/route/remove\n"
        "Disallow: /races/*/riders/\n"
        "Disallow: /races/*/race-rider/*/manual-times\n"
        "Disallow: /races/*/race-rider/*/confirm-finish\n"
        "Sitemap: https://kooksnylive.co.za/sitemap.xml\n"
    )

    return Response(content, mimetype="text/plain")


@bp_home.route("/sitemap.xml")
def sitemap():
    """
    Return the canonical public pages currently intended for search indexing.

    Output:
      XML sitemap containing the production landing page and public race
      dashboard as fully qualified canonical URLs.

    Notes:
      The public rider-profile route remains excluded while it is a placeholder.
      Add it, public race detail pages, and public result pages when each exposes
      stable, distinct content that should appear in search results.
    """
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://kooksnylive.co.za/</loc>
  </url>
  <url>
    <loc>https://kooksnylive.co.za/dashboard</loc>
  </url>
</urlset>
"""

    return Response(sitemap_xml, mimetype="application/xml")


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
