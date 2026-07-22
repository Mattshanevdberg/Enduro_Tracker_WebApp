"""
Landing, crawler, sitemap, and public/admin dashboard HTTP controllers.

Routes
------
GET /
    Render the public landing page.
GET /robots.txt
    Return production crawler guidance and the canonical sitemap location.
GET /sitemap.xml
    Return canonical public pages and durable public rider profiles.
GET /dashboard
    Render categorized races and all riders in the public dashboard.
GET /dashboard-admin
    Render the admin operational dashboard with every race lifecycle state.

Dashboard queries and durable display preparation live in src.services.home.
This module owns Flask request parsing, access control, session boundaries,
presentation copy, URL-independent tab selection, and template responses.
"""

from flask import Blueprint, Response, render_template, request

from src.auth.decorators import admin_required
from src.db.models import SessionLocal
from src.services.home import (
    load_dashboard_display_data,
    load_race_display_data,
    list_public_rider_ids,
)


bp_home = Blueprint("home", __name__)

PUBLIC_DASHBOARD_TABS = ("upcoming", "live", "past", "riders")
DASHBOARD_TAB_PRESENTATION = {
    "upcoming": {
        "label": "Upcoming Races",
        "eyebrow": "Prepare for the next challenge",
        "title": "Upcoming Races",
        "message": "Find your next race, prepare your equipment and secure your tracker before race day.",
        "heading": "Upcoming races",
        "subheading": "Browse the next scheduled enduro and motocross events.",
        "hero_image": "images/dashboard/heroes/upcoming.webp",
    },
    "live": {
        "label": "Live Races",
        "eyebrow": "Tracking now",
        "title": "Live Races",
        "message": "Follow rider movement, timing and race progress as the event unfolds.",
        "heading": "Live now",
        "subheading": "Select a race to open its live tracking page.",
        "hero_image": "images/dashboard/heroes/live.webp",
    },
    "past": {
        "label": "Past Races",
        "eyebrow": "Relive the race",
        "title": "Past Races",
        "message": "Review completed events, compare results and explore the routes riders completed.",
        "heading": "Past races",
        "subheading": "Open an event to view its race page or select Results directly.",
        "hero_image": "images/dashboard/heroes/past.webp",
    },
    "riders": {
        "label": "Riders",
        "eyebrow": "Meet the field",
        "title": "Riders",
        "message": "Explore every rider profile, bike, team and biography.",
        "heading": "Rider profiles",
        "subheading": "Select a rider to view their complete public profile.",
        "hero_image": "images/dashboard/heroes/riders.webp",
    },
}


def _selected_dashboard_tab(raw_tab: str | None) -> str:
    """
    Resolve a safe dashboard tab from the public query parameter.

    Input Args:
      raw_tab: optional request query value.

    Output:
      One supported tab key, defaulting to upcoming.
    """
    return raw_tab if raw_tab in PUBLIC_DASHBOARD_TABS else "upcoming"


def _render_public_dashboard():
    """
    Render the public dashboard from service-composed race and rider data.

    Output:
      Rendered dashboard.html response.
    """
    session = SessionLocal()
    try:
        page_data = load_dashboard_display_data(session)
        return render_template(
            "dashboard.html",
            **page_data,
            tab_presentation=DASHBOARD_TAB_PRESENTATION,
            selected_tab=_selected_dashboard_tab(request.args.get("tab")),
        )
    finally:
        session.close()


def _render_admin_dashboard():
    """
    Render the admin dashboard from service-prepared race data.

    Output:
      Rendered dashboard_admin.html response.
    """
    session = SessionLocal()
    try:
        return render_template(
            "dashboard_admin.html",
            races=load_race_display_data(session),
        )
    finally:
        session.close()


@bp_home.route("/")
def home_page():
    """Render the public landing page."""
    return render_template("landing.html")


@bp_home.route("/robots.txt")
def robots_txt():
    """
    Tell cooperative crawlers which public pages they may visit.

    Output:
      Plain-text guidance excluding authenticated/operational paths and
      advertising the canonical production sitemap. This is not access control.
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
    Return canonical public pages currently intended for search indexing.

    Output:
      XML sitemap containing the landing page, public dashboard, and durable
      rider-detail pages as fully qualified canonical production URLs.
    """
    session = SessionLocal()
    try:
        rider_urls = "".join(
            "  <url>\n"
            f"    <loc>https://kooksnylive.co.za/rider/{rider_id}</loc>\n"
            "  </url>\n"
            for rider_id in list_public_rider_ids(session)
        )
    finally:
        session.close()
    sitemap_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url>\n"
        "    <loc>https://kooksnylive.co.za/</loc>\n"
        "  </url>\n"
        "  <url>\n"
        "    <loc>https://kooksnylive.co.za/dashboard</loc>\n"
        "  </url>\n"
        f"{rider_urls}"
        "</urlset>\n"
    )
    return Response(sitemap_xml, mimetype="application/xml")


@bp_home.route("/dashboard")
def dashboard():
    """Render the public tabbed race and rider dashboard."""
    return _render_public_dashboard()


@bp_home.route("/dashboard-admin")
@admin_required
def dashboard_admin():
    """Render the admin operational dashboard with all race statuses."""
    return _render_admin_dashboard()
