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


def user_can_access_rider_resource(user, rider_id) -> bool:
    """
    Check whether a user can access a resource owned by a Rider row.

    Input Args:
      user: Flask-Login current user object or a User model row.
      rider_id: Rider primary key that owns the requested resource.

    Output:
      True when the user is an active admin, or when the user is an active
      rider whose linked user.rider_id matches the requested rider_id.

    Notes:
      This helper is intentionally generic. It can protect direct Rider profile
      edits, RaceRider entries, and any future resource that is owned through a
      riders.id value.
    """
    if user_has_role(user, {"admin"}):
        return True

    if not user_has_role(user, {"rider"}):
        return False

    try:
        requested_rider_id = int(rider_id)
    except (TypeError, ValueError):
        return False

    return getattr(user, "rider_id", None) == requested_rider_id


def require_rider_resource_access(user, rider_id) -> None:
    """
    Abort unless a user can access a Rider-owned resource.

    Input Args:
      user: Flask-Login current user object or a User model row.
      rider_id: Rider primary key that owns the requested resource.

    Output:
      None when access is allowed.

    Raises:
      403 Forbidden when the user is not an admin and does not own the linked
      Rider resource.
    """
    if not user_can_access_rider_resource(user, rider_id):
        abort(403)


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
