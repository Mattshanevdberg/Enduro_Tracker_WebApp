"""
Map tile quota helpers for Esri/satellite usage controls.

This module contains reusable helper logic only. It does not define Flask
routes and does not make Redis or database connections itself. Route modules
should pass in the current request/response objects, Redis client, SQLAlchemy
rows, and configured limits.

Responsibilities:
- Create or reuse an anonymous browser cookie id.
- Build stable Redis keys for per-browser tile counters and block flags.
- Increment short-term browser tile counters with expiry.
- Check/set/reset short-term Redis browser blocks.
- Check/set/reset monthly database quota block flags.
- Apply tile deltas to summarized usage and monthly quota rows.

Contains:
- _timeout_seconds: convert timeout minutes to seconds for Redis expiry.
- _normalise_tile_delta: validate tile deltas before counting them.
- _minute_bucket: round a datetime down to its minute bucket.
- _window_bucket_times: list the minute buckets in the rolling usage window.
- _is_safe_browser_cookie_id: check browser cookie ids before reusing them.
- generate_browser_cookie_id: create a new anonymous browser identifier.
- get_or_create_browser_cookie_id: read or create the anonymous browser cookie.
- browser_count_key: build the Redis key for one browser/minute tile bucket.
- browser_block_key: build the Redis key for a browser's temporary block flag.
- _redis_value_to_int: safely parse Redis values into integers.
- get_browser_tile_count: sum browser tile buckets inside the rolling window.
- increment_browser_tile_count: add a tile delta to the current minute bucket.
- is_browser_over_tile_limit: compare rolling browser usage against the tile limit.
- _browser_window_count_keys: build current rolling-window Redis count keys.
- is_browser_blocked: check whether a browser currently has a block flag.
- set_browser_block: set a temporary browser block in Redis.
- reset_browser_block: remove a browser block and optionally current window counters.
- _override_is_current: check whether a monthly quota override is still active.
- is_monthly_blocked: decide whether monthly/global quota state blocks satellite use.
- set_monthly_hard_stop: set or clear the monthly hard-stop flag.
- reset_monthly_hard_stop: clear the monthly hard-stop flag.
- record_tile_delta: apply one tile delta to session and monthly DB rows.

Notes:
- Browser cookies identify a browser only; they do not prove a real person.
- Redis is the short-term enforcement store.
- Database rows remain the durable monthly/admin source of truth.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from src.utils.time import utc_now


MAP_BROWSER_COOKIE_NAME = "map_browser_id"
REDIS_BROWSER_COUNT_PREFIX = "map_tiles:browser"
REDIS_BROWSER_BLOCK_PREFIX = "map_tiles:browser"
DEFAULT_BROWSER_COOKIE_MAX_AGE_DAYS = 365

_SAFE_BROWSER_COOKIE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


def _timeout_seconds(timeout_minutes: int | str | None) -> int:
    """
    Convert a timeout in minutes to seconds.

    Input Args:
      timeout_minutes: configured timeout value, usually MAP_USER_LIMIT_TIMEOUT_MIN.

    Output:
      Positive integer timeout in seconds.

    Raises:
      ValueError when the timeout is missing, zero, negative, or not an integer.
    """
    try:
        minutes = int(timeout_minutes)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_minutes must be a positive integer") from exc

    if minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")
    return minutes * 60


def _normalise_tile_delta(tiles_delta: int | str) -> int:
    """
    Validate and normalise a tile usage delta.

    Input Args:
      tiles_delta: number of newly observed tiles since the previous report.

    Output:
      Non-negative integer tile delta.

    Raises:
      ValueError when the delta is negative or cannot be parsed as an integer.
    """
    try:
        delta = int(tiles_delta)
    except (TypeError, ValueError) as exc:
        raise ValueError("tiles_delta must be a non-negative integer") from exc

    if delta < 0:
        raise ValueError("tiles_delta must be a non-negative integer")
    return delta


def _minute_bucket(value: datetime | None = None) -> datetime:
    """
    Round a datetime down to the minute for Redis time-bucket counting.

    Input Args:
      value: optional datetime. When missing, the current UTC time is used.

    Output:
      Datetime with seconds and microseconds set to zero.
    """
    current_time = value or utc_now()
    return current_time.replace(second=0, microsecond=0)


def _window_bucket_times(timeout_minutes: int | str, now: datetime | None = None) -> list[datetime]:
    """
    Build the minute buckets that make up the rolling usage window.

    Input Args:
      timeout_minutes: number of minutes in the rolling usage window.
      now: optional current UTC datetime for deterministic tests.

    Output:
      List of minute bucket datetimes from oldest to newest.

    Notes:
      This is minute-granularity rolling-window enforcement. With a 30-minute
      window, the current minute and previous 29 minute buckets are counted.
    """
    minutes = _timeout_seconds(timeout_minutes) // 60
    current_bucket = _minute_bucket(now)
    return [current_bucket - timedelta(minutes=offset) for offset in range(minutes - 1, -1, -1)]


def _is_safe_browser_cookie_id(browser_cookie_id: str | None) -> bool:
    """
    Check whether a browser cookie id has the expected safe token shape.

    Input Args:
      browser_cookie_id: raw cookie value supplied by the browser.

    Output:
      True when the id is non-empty and contains only URL-safe token characters.
    """
    return bool(browser_cookie_id and _SAFE_BROWSER_COOKIE_ID_RE.fullmatch(browser_cookie_id))


def generate_browser_cookie_id() -> str:
    """
    Generate a new random browser cookie id.

    Input Args:
      None.

    Output:
      URL-safe random token used only as an anonymous browser identifier.
    """
    return secrets.token_urlsafe(32)


def get_or_create_browser_cookie_id(
    request,
    response,
    cookie_name: str = MAP_BROWSER_COOKIE_NAME,
    secure: bool = True,
    same_site: str = "Lax",
    max_age_days: int = DEFAULT_BROWSER_COOKIE_MAX_AGE_DAYS,
) -> str:
    """
    Read or create the anonymous browser id cookie.

    Input Args:
      request: Flask request-like object with a cookies mapping.
      response: Flask response-like object with set_cookie().
      cookie_name: cookie name to read/write.
      secure: whether the browser should only send the cookie over HTTPS.
      same_site: SameSite cookie policy.
      max_age_days: cookie lifetime in days.

    Output:
      Existing safe browser id or a newly generated id.

    Notes:
      The cookie contains only an identifier. Tile counts, block flags, and
      usage analytics stay server-side in Redis/database stores.
    """
    existing = (getattr(request, "cookies", {}) or {}).get(cookie_name)
    if _is_safe_browser_cookie_id(existing):
        return existing

    browser_cookie_id = generate_browser_cookie_id()
    response.set_cookie(
        cookie_name,
        browser_cookie_id,
        max_age=int(max_age_days) * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite=same_site,
    )
    return browser_cookie_id


def browser_count_key(browser_cookie_id: str, bucket_time: datetime | None = None) -> str:
    """
    Build the Redis key for one browser/minute tile count bucket.

    Input Args:
      browser_cookie_id: anonymous browser cookie id.
      bucket_time: optional datetime identifying the minute bucket.

    Output:
      Redis key string.
    """
    bucket = _minute_bucket(bucket_time).strftime("%Y%m%d%H%M")
    return f"{REDIS_BROWSER_COUNT_PREFIX}:{browser_cookie_id}:count:{bucket}"


def browser_block_key(browser_cookie_id: str) -> str:
    """
    Build the Redis key for a browser's temporary satellite block.

    Input Args:
      browser_cookie_id: anonymous browser cookie id.

    Output:
      Redis key string.
    """
    return f"{REDIS_BROWSER_BLOCK_PREFIX}:{browser_cookie_id}:blocked"


def _redis_value_to_int(value) -> int:
    """
    Convert a Redis value into an integer.

    Input Args:
      value: Redis return value, commonly bytes, str, int, or None.

    Output:
      Parsed integer, or 0 for missing/unparseable values.
    """
    if value is None:
        return 0
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_browser_tile_count(
    redis_client,
    browser_cookie_id: str,
    timeout_minutes: int | str,
    now: datetime | None = None,
) -> int:
    """
    Sum the browser's Redis tile count across the rolling usage window.

    Input Args:
      redis_client: Redis client-like object with get().
      browser_cookie_id: anonymous browser cookie id.
      timeout_minutes: number of minutes in the rolling usage window.
      now: optional current UTC datetime for deterministic tests.

    Output:
      Tile count inside the rolling usage window.
    """
    total = 0
    for bucket_time in _window_bucket_times(timeout_minutes, now=now):
        total += _redis_value_to_int(redis_client.get(browser_count_key(browser_cookie_id, bucket_time)))
    return total


def increment_browser_tile_count(
    redis_client,
    browser_cookie_id: str,
    tiles_delta: int | str,
    timeout_minutes: int | str,
    now: datetime | None = None,
) -> int:
    """
    Increment the current browser/minute tile bucket and return rolling usage.

    Input Args:
      redis_client: Redis client-like object with incrby() and expire().
      browser_cookie_id: anonymous browser cookie id.
      tiles_delta: newly observed tile count since the previous browser report.
      timeout_minutes: number of minutes in the rolling usage window.
      now: optional current UTC datetime for deterministic tests.

    Output:
      Updated tile count inside the rolling usage window.

    Notes:
      Tile usage is stored in minute buckets. Each bucket expires after the
      rolling window plus a small buffer. The current rolling total is produced
      by summing the buckets that fall inside the current window.
    """
    delta = _normalise_tile_delta(tiles_delta)
    ttl_seconds = _timeout_seconds(timeout_minutes) + 120
    key = browser_count_key(browser_cookie_id, _minute_bucket(now))
    redis_client.incrby(key, delta)
    redis_client.expire(key, ttl_seconds)
    return get_browser_tile_count(redis_client, browser_cookie_id, timeout_minutes, now=now)


def is_browser_over_tile_limit(
    redis_client,
    browser_cookie_id: str,
    tile_limit: int | str,
    timeout_minutes: int | str,
    now: datetime | None = None,
) -> bool:
    """
    Check whether rolling browser tile usage is over the configured limit.

    Input Args:
      redis_client: Redis client-like object with get().
      browser_cookie_id: anonymous browser cookie id.
      tile_limit: configured browser tile limit, usually MAP_TILE_USER_LIMIT.
      timeout_minutes: rolling usage window, usually MAP_USER_LIMIT_TIMEOUT_MIN.
      now: optional current UTC datetime for deterministic tests.

    Output:
      True when the browser's rolling-window tile count is greater than the limit.
    """
    try:
        limit = int(tile_limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("tile_limit must be a non-negative integer") from exc

    if limit < 0:
        raise ValueError("tile_limit must be a non-negative integer")
    return get_browser_tile_count(redis_client, browser_cookie_id, timeout_minutes, now=now) > limit


def _browser_window_count_keys(
    browser_cookie_id: str,
    timeout_minutes: int | str,
    now: datetime | None = None,
) -> list[str]:
    """
    Build all Redis count keys inside the current rolling usage window.

    Input Args:
      browser_cookie_id: anonymous browser cookie id.
      timeout_minutes: number of minutes in the rolling usage window.
      now: optional current UTC datetime for deterministic tests.

    Output:
      List of Redis count keys inside the current rolling usage window.
    """
    return [browser_count_key(browser_cookie_id, bucket_time) for bucket_time in _window_bucket_times(timeout_minutes, now=now)]


def is_browser_blocked(redis_client, browser_cookie_id: str) -> bool:
    """
    Check whether a browser currently has a Redis block flag.

    Input Args:
      redis_client: Redis client-like object with get().
      browser_cookie_id: anonymous browser cookie id.

    Output:
      True when the browser block key exists.
    """
    return redis_client.get(browser_block_key(browser_cookie_id)) is not None


def set_browser_block(
    redis_client,
    browser_cookie_id: str,
    timeout_minutes: int | str,
    reason: str = "browser_limit",
) -> None:
    """
    Set a temporary Redis browser block.

    Input Args:
      redis_client: Redis client-like object with setex().
      browser_cookie_id: anonymous browser cookie id.
      timeout_minutes: block expiry window in minutes.
      reason: short safe reason stored as the Redis value.

    Output:
      None.
    """
    redis_client.setex(browser_block_key(browser_cookie_id), _timeout_seconds(timeout_minutes), reason)


def reset_browser_block(
    redis_client,
    browser_cookie_id: str,
    reset_count: bool = True,
    timeout_minutes: int | str | None = None,
    now: datetime | None = None,
) -> None:
    """
    Remove a browser's temporary Redis block.

    Input Args:
      redis_client: Redis client-like object with delete().
      browser_cookie_id: anonymous browser cookie id.
      reset_count: when True, also remove current rolling-window tile counters.
      timeout_minutes: required when reset_count is True for rolling-window counts.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None.
    """
    keys = [browser_block_key(browser_cookie_id)]
    if reset_count:
        if timeout_minutes is None:
            raise ValueError("timeout_minutes is required when reset_count is True")
        keys.extend(_browser_window_count_keys(browser_cookie_id, timeout_minutes, now=now))
    redis_client.delete(*keys)


def _override_is_current(monthly_quota, now: datetime | None = None) -> bool:
    """
    Check whether a monthly quota override is currently valid.

    Input Args:
      monthly_quota: MapTileMonthlyQuota-like row.
      now: optional current UTC datetime for deterministic checks.

    Output:
      True when override_active is true and override_until is absent or future.
    """
    if not getattr(monthly_quota, "override_active", False):
        return False

    override_until = getattr(monthly_quota, "override_until", None)
    if override_until is None:
        return True

    current_time = now or utc_now()
    if override_until.tzinfo is None and current_time.tzinfo is not None:
        override_until = override_until.replace(tzinfo=current_time.tzinfo)
    return override_until > current_time


def is_monthly_blocked(
    monthly_quota,
    role: str = "anonymous",
    is_admin: bool = False,
    now: datetime | None = None,
) -> bool:
    """
    Check whether monthly/global quota state blocks satellite config release.

    Input Args:
      monthly_quota: MapTileMonthlyQuota-like row, or None.
      role: current browser role snapshot: anonymous, rider, or admin.
      is_admin: explicit admin flag for callers that already know the user role.
      now: optional current UTC datetime for deterministic override checks.

    Output:
      True when satellite should be blocked by monthly/global state.

    Notes:
      Missing quota state fails closed for non-admin users because releasing an
      Esri key without a quota row removes global hard-stop protection.
    """
    if monthly_quota is None:
        return not is_admin

    if _override_is_current(monthly_quota, now=now):
        return False

    if getattr(monthly_quota, "viewers_only_blocked", False) and role == "anonymous":
        return True

    estimated = int(getattr(monthly_quota, "estimated_tiles_used", 0) or 0)
    hard_stop = int(getattr(monthly_quota, "hard_stop_threshold", 0) or 0)
    if getattr(monthly_quota, "hard_stop_active", False):
        return True
    if hard_stop > 0 and estimated >= hard_stop:
        return True
    return False


def set_monthly_hard_stop(monthly_quota, active: bool = True, now: datetime | None = None) -> None:
    """
    Set or clear the monthly hard-stop flag.

    Input Args:
      monthly_quota: MapTileMonthlyQuota-like row to update.
      active: desired hard-stop state.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the database session.
    """
    monthly_quota.hard_stop_active = bool(active)
    if active and getattr(monthly_quota, "hard_stop_triggered_at", None) is None:
        monthly_quota.hard_stop_triggered_at = now or utc_now()
    monthly_quota.updated_at = now or utc_now()


def reset_monthly_hard_stop(monthly_quota, now: datetime | None = None) -> None:
    """
    Clear the monthly hard-stop flag.

    Input Args:
      monthly_quota: MapTileMonthlyQuota-like row to update.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the database session.
    """
    monthly_quota.hard_stop_active = False
    monthly_quota.updated_at = now or utc_now()


def record_tile_delta(monthly_quota, usage_session, tiles_delta: int | str, now: datetime | None = None) -> int:
    """
    Apply one accepted tile delta to monthly and session DB rows.

    Input Args:
      monthly_quota: MapTileMonthlyQuota-like row.
      usage_session: MapTileUsageSession-like row.
      tiles_delta: newly observed tile count since previous browser report.
      now: optional current UTC datetime for deterministic tests.

    Output:
      Normalised tile delta that was applied.

    Notes:
      This is the central double-counting control. Browser reports should send
      deltas, not cumulative totals, so each delta can be applied once to both
      the summarized usage session and the monthly quota row.
    """
    delta = _normalise_tile_delta(tiles_delta)
    current_time = now or utc_now()

    usage_session.estimated_tiles_loaded = int(getattr(usage_session, "estimated_tiles_loaded", 0) or 0) + delta
    usage_session.session_last_seen_at = current_time
    usage_session.updated_at = current_time

    monthly_quota.estimated_tiles_used = int(getattr(monthly_quota, "estimated_tiles_used", 0) or 0) + delta
    monthly_quota.updated_at = current_time
    return delta
