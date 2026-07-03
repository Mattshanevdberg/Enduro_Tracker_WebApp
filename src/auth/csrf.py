"""
CSRF protection setup for browser form and JSON POST requests.

CSRF means Cross-Site Request Forgery. Flask-WTF provides the protection layer
that checks a per-session token on state-changing browser requests. Existing
legacy forms are migrated gradually, so this module also provides a clear helper
for temporary blueprint exemptions during the staged auth implementation.
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
    Temporarily exempt existing unconverted blueprints from CSRF enforcement.

    Input Args:
      blueprints: Flask Blueprint objects whose routes should not be checked by
        CSRFProtect yet.

    Output:
      None. The provided blueprints are registered as CSRF-exempt.

    Notes:
      This is a staged migration helper. New authentication routes should not be
      exempt; existing forms should be removed from this list as their templates
      and JavaScript POST calls gain CSRF tokens.
    """
    for blueprint in blueprints:
        csrf.exempt(blueprint)
