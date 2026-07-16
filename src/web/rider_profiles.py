"""
Public rider profile routes.

This module remains web-only because the current placeholder has no model access,
business rules, or reusable parsing to extract into utility/service modules. The
full page will later show rider profiles and expose edit actions only to the
linked rider or admins.

Paths
-----
GET /rider -> Public rider profile index placeholder
"""

from flask import Blueprint, render_template, url_for


bp_rider_profiles = Blueprint("rider_profiles", __name__)


@bp_rider_profiles.route("/rider", methods=["GET"])
def rider_profiles():
    """
    Render the public rider profiles placeholder.

    Input Args:
      None.

    Output:
      Rendered placeholder.html response for the future rider profiles experience.

    Notes:
      url_for and render_template are Flask-specific response concerns, so the
      current placeholder correctly remains entirely in the web layer.
    """
    return render_template(
        "placeholder.html",
        title="Rider Profiles",
        description="Public rider profile page with future rider/admin edit controls.",
        route="/rider",
        access="all viewers; edit controls later restricted to linked rider/admin",
        back_url=url_for("home.dashboard"),
        back_label="Back to Dashboard",
    )
