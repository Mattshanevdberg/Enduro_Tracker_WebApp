"""
Public rider profile routes.

This module currently provides placeholder routing for the future rider profile
index. The full page will later show rider profiles and expose edit actions only
to the linked rider or admins.

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
      Placeholder page for the future rider profiles experience.
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
