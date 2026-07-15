"""
Rate-limit setup for authentication and other abuse-sensitive browser actions.

Flask-Limiter stores counters in Redis through AUTH_RATE_LIMIT_STORAGE_URL. No
route-specific limits are applied in this setup step; those are added directly
to login, signup, and password-reset routes when those routes are introduced.
"""

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    headers_enabled=True,
)


def init_limiter(app: Flask) -> None:
    """
    Attach Flask-Limiter to the Flask application using Redis-backed storage.

    Input Args:
      app: Flask application instance.

    Output:
      None. The Flask app gains a shared Limiter extension for later route
      decorators.

    Raises:
      RuntimeError when AUTH_RATE_LIMIT_STORAGE_URL is missing, because falling
      back to process memory would make rate limits inconsistent across workers.
    """
    storage_uri = (app.config.get("AUTH_RATE_LIMIT_STORAGE_URL") or "").strip()
    if not storage_uri:
        raise RuntimeError("AUTH_RATE_LIMIT_STORAGE_URL is required for Flask-Limiter.")

    app.config["RATELIMIT_STORAGE_URI"] = storage_uri
    app.config["RATELIMIT_HEADERS_ENABLED"] = True
    limiter.init_app(app)
