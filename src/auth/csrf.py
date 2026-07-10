"""
CSRF protection setup for browser form and JSON POST requests.

CSRF means Cross-Site Request Forgery. Flask-WTF provides the protection layer
that checks a per-session token on state-changing browser requests. Browser
forms should include a hidden csrf_token field, while browser JavaScript POST
requests should send the same token in the X-CSRFToken header.

Device ingest routes are not browser-session routes, so they stay exempt from
CSRF and should instead be protected with device/API credentials.
"""

from flask import Blueprint
from flask_wtf.csrf import CSRFProtect


csrf = CSRFProtect()


def init_csrf(app) -> None:
    """
    Attach Flask-WTF CSRF protection to the Flask application.

    Input Args:
      app: Flask application instance.

    Output:
      None. The Flask app gains CSRF request checking and template helpers.
    """
    csrf.init_app(app)


def exempt_blueprints(*blueprints: Blueprint) -> None:
    """
    Exempt non-browser-session blueprints from CSRF enforcement.

    Input Args:
      blueprints: Flask Blueprint objects whose routes should not be checked by
        CSRFProtect.

    Output:
      None. The provided blueprints are registered as CSRF-exempt.

    Notes:
      This should be used sparingly. Browser form blueprints should normally
      remain protected. Tracker/device API blueprints may be exempt because
      they do not use browser cookies and should use API/device authentication
      instead.
    """
    for blueprint in blueprints:
        csrf.exempt(blueprint)
