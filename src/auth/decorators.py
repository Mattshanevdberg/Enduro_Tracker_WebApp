"""
Reusable route access-control decorators.

Viewer access remains anonymous by default. These decorators are only applied
to routes that require an authenticated rider or admin account, matching the
viewer/rider/admin split described in the system design.
"""

from functools import wraps

from flask import abort
from flask_login import current_user, login_required


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


def active_user_required(func):
    """
    Require any logged-in active user before allowing a route to run.

    Input Args:
      func: Flask route function being protected.

    Output:
      Wrapped route function.

    Notes:
      Anonymous, deleted, inactive, and auth-version-stale users are redirected
      through Flask-Login because load_user() returns None for invalid sessions.
    """
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
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
    @login_required
    def wrapper(*args, **kwargs):
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
    @login_required
    def wrapper(*args, **kwargs):
        if not user_has_role(current_user, {"admin"}):
            abort(403)
        return func(*args, **kwargs)

    return wrapper
