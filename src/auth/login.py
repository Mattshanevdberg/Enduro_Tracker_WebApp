"""
Flask-Login setup for browser user sessions.

This module connects Flask-Login's browser session handling to the users table.
It reloads the current user for each request and keeps a lightweight
auth-version value in the signed Flask session so old sessions can be rejected
after password resets or other sensitive account changes.
"""

from flask import session as flask_session
from flask_login import LoginManager

from src.db.models import SessionLocal, User


AUTH_VERSION_SESSION_KEY = "auth_version"

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in as rider to access this page."
login_manager.login_message_category = "warning"


def remember_auth_version(user) -> None:
    """
    Store a user's current auth_version in the signed browser session.

    Input Args:
      user: User row that has just successfully logged in.

    Output:
      None.

    Notes:
      The login route should call this immediately after Flask-Login's
      login_user(user). load_user() later compares this stored value with the
      database value to invalidate stale sessions after password resets or
      forced account changes.
    """
    flask_session[AUTH_VERSION_SESSION_KEY] = int(getattr(user, "auth_version", 0) or 0)


def clear_auth_version() -> None:
    """
    Remove the stored auth_version from the signed browser session.

    Input Args:
      None.

    Output:
      None.

    Notes:
      Logout and forced-session-clear flows can call this alongside
      Flask-Login's logout_user().
    """
    flask_session.pop(AUTH_VERSION_SESSION_KEY, None)


@login_manager.user_loader
def load_user(user_id: str):
    """
    Load the current browser user from the Flask session.

    Input Args:
      user_id: string user id stored by Flask-Login in the session cookie.

    Output:
      Active User row when the id and session auth_version are valid; otherwise
      None.

    Notes:
      Returning None makes Flask-Login treat the request as anonymous. That
      cleanly redirects anonymous, deleted, inactive, or stale-session users to
      the configured login route when they access protected pages.
    """
    try:
        user_pk = int(user_id)
    except (TypeError, ValueError):
        return None

    session = SessionLocal()
    try:
        user = session.get(User, user_pk)
        if not user:
            return None
        if not getattr(user, "is_active", False):
            return None

        session_auth_version = flask_session.get(AUTH_VERSION_SESSION_KEY)
        try:
            session_auth_version = int(session_auth_version)
        except (TypeError, ValueError):
            return None

        if session_auth_version != int(getattr(user, "auth_version", 0) or 0):
            return None

        # Detach the user object before the short-lived SQLAlchemy session is
        # closed. Flask-Login keeps this object around for the request only; any
        # later database work should open its own session.
        session.expunge(user)
        return user
    finally:
        session.close()
