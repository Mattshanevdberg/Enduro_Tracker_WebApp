"""
Flask-Login setup for browser user sessions.

This module is intentionally small and import-safe during the current auth
setup phase. The User table/model is added in Step 3, so the user loader checks
for that model dynamically and returns None until it exists.
"""

from flask_login import LoginManager

from src.db import models as db_models
from src.db.models import SessionLocal


login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id: str):
    """
    Load the current browser user from the Flask session.

    Input Args:
      user_id: string user id stored by Flask-Login in the session cookie.

    Output:
      User row when the id is valid; otherwise None.

    Notes:
      The User model is introduced in the next database step. Until then this
      loader deliberately returns None so Flask can start without an auth table.
      Active-state enforcement is handled by the route decorators so an account
      disabled after login can be logged out and blocked consistently.
    """
    User = getattr(db_models, "User", None)
    if User is None:
        return None

    try:
        user_pk = int(user_id)
    except (TypeError, ValueError):
        return None

    session = SessionLocal()
    try:
        user = session.get(User, user_pk)
        if not user:
            return None

        # Detach the user object before the short-lived SQLAlchemy session is
        # closed. Flask-Login keeps this object around for the request only; any
        # later database work should open its own session.
        session.expunge(user)
        return user
    finally:
        session.close()
