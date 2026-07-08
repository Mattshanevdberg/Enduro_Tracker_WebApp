"""
Flask app entrypoint. Registers the ingest blueprint.
"""

#### for running in vscode (comment out when on Raspberry Pi)
import sys
import os
from datetime import timedelta

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
####

from flask import Flask, jsonify
from src.auth.csrf import exempt_blueprints, init_csrf
from src.auth.login import login_manager
from src.auth.rate_limits import init_limiter
from src.auth.routes import bp_auth
from src.utils.env import env_bool

# blueprint imports
from src.api.ingest import bp as ingest_bp
from src.web.home import bp_home
from src.web.riders import bp_riders
from src.web.devices import bp_devices
from src.web.races import bp_races
from src.web.rfid import bp_rfid

# regular imports
import yaml
from pathlib import Path

# Load configuration from JSON file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../configs/config.yaml')
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

#set globals
# DATABASE_URL = config['global']['database_url'] # not used
API_HOST = config['global']['api_host']
API_PORT = config['global']['api_port']

def create_app():
    app = Flask(
        __name__, 
        template_folder="../templates" # point Flask to your templates folder (repo root/templates)
    )
    app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]

    # Harden browser session cookies before any login routes are introduced.
    # Secure is environment-driven so dev/prod can require HTTPS while a future
    # plain-localhost workflow can opt out deliberately if needed.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax").strip() or "Lax"
    app.config["SESSION_COOKIE_SECURE"] = env_bool("SESSION_COOKIE_SECURE", default=True)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    # Keep map-provider configuration in Flask rather than in committed client
    # code. The ArcGIS API key is intentionally a browser-facing API key, so the
    # post-race route will expose it only when satellite imagery is configured.
    app.config["MAP_PROVIDER"] = os.environ.get("MAP_PROVIDER", "").strip().lower()
    app.config["MAP_STYLE"] = os.environ.get("MAP_STYLE", "").strip()
    app.config["ARCGIS_API_KEY"] = os.environ.get("ARCGIS_API_KEY", "").strip()
    app.config["MAP_TILE_MONTHLY_LIMIT"] = os.environ.get("MAP_TILE_MONTHLY_LIMIT", "").strip()
    app.config["MAP_TILE_WARNING_THRESHOLD"] = os.environ.get("MAP_TILE_WARNING_THRESHOLD", "").strip()
    app.config["MAP_TILE_HARD_STOP_THRESHOLD"] = os.environ.get("MAP_TILE_HARD_STOP_THRESHOLD", "").strip()
    app.config["MAP_TILE_USER_LIMIT"] = os.environ.get("MAP_TILE_USER_LIMIT", "").strip()
    app.config["MAP_USER_LIMIT_TIMEOUT_MIN"] = os.environ.get("MAP_USER_LIMIT_TIMEOUT_MIN", "").strip()
    app.config["AUTH_RATE_LIMIT_STORAGE_URL"] = os.environ.get("AUTH_RATE_LIMIT_STORAGE_URL", "").strip()

    # Configure browser login-session support before registering blueprints.
    # The User model and /login route are added in later auth steps; initialising
    # Flask-Login here creates the shared session plumbing without changing any
    # current page permissions yet.
    login_manager.init_app(app)

    # Configure Redis-backed rate-limit storage. No route-specific auth limits
    # are applied yet; later /login, /signup, and password-reset routes will use
    # the shared limiter decorators from src.auth.rate_limits.
    init_limiter(app)

    # Enable CSRF infrastructure now so new browser forms can be protected as
    # they are introduced. Existing management forms and tracker APIs do not
    # include CSRF tokens yet, so those blueprints are temporarily exempted
    # below and will be removed from the exemption list as templates are migrated.
    init_csrf(app)
    exempt_blueprints(
        ingest_bp,
        bp_riders,
        bp_devices,
        bp_races,
    )

    # Root endpoint - decorates the following function, telling Flask to invoke it for GET requests to the root path
    # @app.route("/")
    # def root():
    #     return jsonify({"message": "Enduro Tracker WebApp API"})
    # I have replaced this with the home blueprint

    # Health (liveness)
    @app.route("/api/v1/health") # registers a health-check route so monitoring systems can verify the service is alive
    def health():
        return jsonify({"status": "ok"})

    # Register upload routes
    # attaches the ingest blueprint (with its /api/v1/upload route) to the app; this happens during factory execution so the upload endpoint becomes active.
    app.register_blueprint(ingest_bp) # "/api/v1/upload" endpoint for data ingestion from trackers
    app.register_blueprint(bp_auth) # "/signup" and future auth browser routes
    app.register_blueprint(bp_home) # "/" home page
    app.register_blueprint(bp_riders) # "/riders/new" rider management pages
    app.register_blueprint(bp_devices)  # /devices device management pages
    app.register_blueprint(bp_races)  # /races/* race management pages
    app.register_blueprint(bp_rfid)  # /rfid RFID ingest record viewer
    return app

# For `flask run`
app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ["FLASK_DEBUG"], host=API_HOST, port=API_PORT)
