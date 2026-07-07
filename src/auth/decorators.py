"""
Reusable route access-control decorators.

Viewer access remains anonymous by default. These decorators are only applied
to routes that require an authenticated rider or admin account, matching the
viewer/rider/admin split described in the system design.
"""

from functools import wraps

from flask import abort, current_app
from flask_login import current_user, logout_user


def user_has_role(user, allowed_roles) -> bool:
    """
    Check whether a user is active and has one of the allowed roles.

    Input Args:
      user: Flask-Login current user object or a User model row.
      allowed_roles: iterable of allowed role strings.

    Output:
      True when the user is authenticated, active, and has an allowed role;
      otherwise False.
    """
    roles = {str(role).strip().lower() for role in allowed_roles or set()}
    if not roles:
        return False

    if not getattr(user, "is_authenticated", False):
        return False

    if not getattr(user, "is_active", False):
        return False

    user_role = str(getattr(user, "role", "") or "").strip().lower()
    return user_role in roles


def _active_user_failure_response():
    """
    Return the correct failure response for non-active access attempts.

    Input Args:
      None. The check reads Flask-Login's current_user proxy.

    Output:
      None when the current user is authenticated and active; otherwise a Flask
      response for anonymous users. Inactive users are logged out and aborted.

    Notes:
      This helper intentionally does not use Flask-Login's @login_required
      decorator. Flask-Login treats inactive users as unauthenticated, which
      would redirect them to login before we can explicitly log them out and
      return 403.
    """
    if getattr(current_user, "is_anonymous", True):
        return current_app.login_manager.unauthorized()

    if not getattr(current_user, "is_active", False):
        logout_user()
        abort(403)

    if not getattr(current_user, "is_authenticated", False):
        return current_app.login_manager.unauthorized()

    return None


def active_user_required(func):
    """
    Require any logged-in active user before allowing a route to run.

    Input Args:
      func: Flask route function being protected.

    Output:
      Wrapped route function.

    Notes:
      Anonymous users are redirected through Flask-Login. Inactive users are
      logged out and blocked with 403 so stale browser sessions cannot continue
      to access protected pages.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        failure_response = _active_user_failure_response()
        if failure_response is not None:
            return failure_response
        return func(*args, **kwargs)

    return wrapper


def rider_required(func):
    """
    Require a rider-level account before allowing a route to run.

    Input Args:
      func: Flask route function being protected.

    Output:
      Wrapped route function.

    Notes:
      Admin users are included because admins have the highest permission level.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        failure_response = _active_user_failure_response()
        if failure_response is not None:
            return failure_response
        if not user_has_role(current_user, {"rider", "admin"}):
            abort(403)
        return func(*args, **kwargs)

    return wrapper


def admin_required(func):
    """
    Require an admin account before allowing a route to run.

    Input Args:
      func: Flask route function being protected.

    Output:
      Wrapped route function.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        failure_response = _active_user_failure_response()
        if failure_response is not None:
            return failure_response
        if not user_has_role(current_user, {"admin"}):
            abort(403)
        return func(*args, **kwargs)

    return wrapper
