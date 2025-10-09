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
from src.api.ingest import bp as ingest_bp

def create_app():
    app = Flask(__name__)
    CORS(app) # enables Cross-Origin Resource Sharing on the app so browsers from other origins can call the API

    # Root endpoint - decorates the following function, telling Flask to invoke it for GET requests to the root path
    @app.route("/")
    def root():
        return jsonify({"message": "Enduro Tracker WebApp API"})

    # Health (liveness)
    @app.route("/api/v1/health") # registers a health-check route so monitoring systems can verify the service is alive
    def health():
        return jsonify({"status": "ok"})

    # Register upload routes
    # attaches the ingest blueprint (with its /api/v1/upload route) to the app; this happens during factory execution so the upload endpoint becomes active.
    app.register_blueprint(ingest_bp)
    return app

# For `flask run`
app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

