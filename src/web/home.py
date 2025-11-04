"""
Home page routes (simple navigation hub).
"""

from flask import Blueprint, render_template

bp_home = Blueprint("home", __name__)

@bp_home.route("/")
def home_page():
    # Render the home page with simple navigation buttons
    return render_template("home.html")
