"""
Flask app entrypoint. Registers the ingest blueprint.
"""

#### for running in vscode (comment out when on Raspberry Pi)
import sys
import os

VSCODE_TEST = True  # set to False when running on Raspberry Pi

if VSCODE_TEST:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
####

from flask import Flask, jsonify
from flask_cors import CORS

# blueprint imports
from src.api.ingest import bp as ingest_bp
from src.web.home import bp_home
from src.web.riders import bp_riders
from src.web.devices import bp_devices
from src.web.races import bp_races

# regular imports
import yaml
from pathlib import Path

# Load configuration from JSON file
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../configs/config.yaml')
with open(CONFIG_PATH, 'r') as f:
    config = yaml.safe_load(f)

#set globals
DATABASE_URL = config['global']['database_url']
API_HOST = config['global']['api_host']
API_PORT = config['global']['api_port']

def create_app():
    app = Flask(
        __name__, 
        template_folder="../templates" # point Flask to your templates folder (repo root/templates)
        )
    CORS(app) # enables Cross-Origin Resource Sharing on the app so browsers from other origins can call the API

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
    app.register_blueprint(bp_home) # "/" home page
    app.register_blueprint(bp_riders) # "/riders/new" rider management pages
    app.register_blueprint(bp_devices)  # /devices device management pages
    app.register_blueprint(bp_races)  # /races/* race management pages
    return app

# For `flask run`
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host=API_HOST, port=API_PORT)

