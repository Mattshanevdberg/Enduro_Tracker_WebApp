"""
One-time authentication token helpers.

These helpers are used for password-reset links. A raw token is generated and
emailed to the user once, while only a peppered hash of that token is stored in
the database.
"""

import hashlib
import os
import secrets
from datetime import timedelta

from src.db.models import AuthToken
from src.utils.time import as_aware_utc, utc_now


PASSWORD_RESET_PURPOSE = "password_reset"
DEFAULT_TOKEN_BYTES = 32


def _token_pepper() -> str:
    """
    Read the server-side token pepper from the environment.

    Input Args:
      None.

    Output:
      AUTH_TOKEN_PEPPER value.

    Raises:
      RuntimeError when AUTH_TOKEN_PEPPER is missing, because token hashes should
      not be generated without the server-side secret.
    """
    pepper = (os.environ.get("AUTH_TOKEN_PEPPER") or "").strip()
    if not pepper:
        raise RuntimeError("AUTH_TOKEN_PEPPER is required for auth token hashing.")
    return pepper


def generate_raw_token() -> str:
    """
    Generate a random URL-safe token for a password-reset link.

    Input Args:
      None.

    Output:
      Raw token string. This value is emailed to the user and must never be
      stored directly in the database.
    """
    return secrets.token_urlsafe(DEFAULT_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    """
    Hash a raw auth token with the server-side pepper.

    Input Args:
      raw_token: raw token from the reset link.

    Output:
      SHA-256 hex digest to store in auth_tokens.token_hash.
    """
    token = (raw_token or "").strip()
    material = f"{token}:{_token_pepper()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def invalidate_existing_tokens(session, user_id: int, purpose: str) -> int:
    """
    Mark older unused tokens for one user/purpose as consumed.

    Input Args:
      session: active SQLAlchemy session.
      user_id: users.id value whose tokens should be invalidated.
      purpose: token purpose, currently password_reset.

    Output:
      Number of tokens that were invalidated.
    """
    now = utc_now()
    tokens = (
        session.query(AuthToken)
        .filter(
            AuthToken.user_id == user_id,
            AuthToken.purpose == purpose,
            AuthToken.used_at.is_(None),
        )
        .all()
    )

    for token in tokens:
        token.used_at = now

    return len(tokens)


def create_auth_token(session, user, purpose: str, expires_in_minutes: int) -> str:
    """
    Create a new one-time auth token row and return the raw token for emailing.

    Input Args:
      session: active SQLAlchemy session.
      user: User row that owns the token.
      purpose: token purpose, currently password_reset.
      expires_in_minutes: minutes until the token should naturally expire.

    Output:
      Raw token string to include in the email reset link.

    Notes:
      The raw token is never stored. Only the peppered token hash is inserted
      into auth_tokens.token_hash.
    """
    invalidate_existing_tokens(session, user.id, purpose)

    raw_token = generate_raw_token()
    token_row = AuthToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=hash_token(raw_token),
        expires_at=utc_now() + timedelta(minutes=expires_in_minutes),
        used_at=None,
    )
    session.add(token_row)
    return raw_token


def find_valid_token(session, raw_token: str, purpose: str):
    """
    Find a valid, unused, unexpired token for a submitted raw token value.

    Input Args:
      session: active SQLAlchemy session.
      raw_token: raw token value from the reset link.
      purpose: expected token purpose.

    Output:
      AuthToken row when the token exists, has the expected purpose, has not
      been used, and has not expired. Otherwise None.
    """
    token_hash = hash_token(raw_token)
    token = (
        session.query(AuthToken)
        .filter(
            AuthToken.token_hash == token_hash,
            AuthToken.purpose == purpose,
        )
        .one_or_none()
    )

    if not token:
        return None
    if token.used_at is not None:
        return None
    if as_aware_utc(token.expires_at) <= utc_now():
        return None

    return token


def mark_token_used(token) -> None:
    """
    Mark a valid token as consumed.

    Input Args:
      token: AuthToken row to consume.

    Output:
      None. The token.used_at field is set to the current UTC time.
    """
    token.used_at = utc_now()
