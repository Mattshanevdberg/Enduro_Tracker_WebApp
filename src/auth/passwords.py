"""
Password validation and hashing helpers.

These helpers keep password policy and storage rules in one place. Routes should
call `validate_password` before storing a new password, `hash_password` before
saving it, and `check_password` when a user attempts to log in.
"""

import re

from werkzeug.security import check_password_hash, generate_password_hash


MIN_PASSWORD_LENGTH = 6
NUMBER_OR_SPECIAL_RE = re.compile(r"(\d|[^A-Za-z0-9])")


def validate_password(password: str, confirmation: str | None = None) -> list[str]:
    """
    Validate password strength and optional confirmation match.

    Input Args:
      password: raw password submitted by the user.
      confirmation: optional repeated password submitted by the user.

    Output:
      List of human-readable validation errors. An empty list means the password
      passed the current policy.
    """
    errors = []
    candidate = password or ""

    if len(candidate) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")

    if not NUMBER_OR_SPECIAL_RE.search(candidate):
        errors.append("Password must contain at least one number or special character.")

    if confirmation is not None and candidate != (confirmation or ""):
        errors.append("Password and confirmation password must match.")

    return errors


def hash_password(password: str) -> str:
    """
    Hash a raw password for database storage.

    Input Args:
      password: raw password submitted by the user.

    Output:
      Werkzeug password hash string suitable for storing in users.password_hash.
    """
    return generate_password_hash(password)


def check_password(password_hash: str, password: str) -> bool:
    """
    Check a submitted password against a stored password hash.

    Input Args:
      password_hash: stored hash from users.password_hash.
      password: raw password submitted by the user.

    Output:
      True when the password matches the stored hash, otherwise False.
    """
    if not password_hash or not password:
        return False
    return check_password_hash(password_hash, password)
