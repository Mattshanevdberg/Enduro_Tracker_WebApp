"""
Map tile quota business/service helpers.

This module contains database and business-rule helpers for Esri/satellite map
tile quota enforcement. It does not define Flask routes, does not render
templates, and does not create Redis clients.

Contains:
- current_billing_month: calculate the 25th-to-25th billing-cycle key.
- quota_defaults_from_config: convert map-limit config values into quota defaults.
- get_or_create_current_quota: load/create the current billing-cycle quota row.
- get_or_create_usage_session: load/create a summarized browser/page usage row.
- update_quota_threshold_flags: set warning/hard-stop timestamps and flags.
- record_browser_block: record a browser block for admin visibility.
- release_browser_blocks: mark active browser block rows released.
- set_viewers_only_blocked: block/unblock anonymous viewer satellite access.
- set_global_hard_stop: manually set/clear global hard-stop state.
- set_monthly_thresholds: manually update active monthly quota thresholds.
- set_monthly_tile_estimate: manually correct the estimated monthly tile count.
- set_monthly_override: enable a temporary monthly hard-stop override.
- clear_monthly_override: disable a monthly hard-stop override.
- record_quota_audit_event: write admin/system quota actions to auth_audit_events.
- monthly_block_reason: convert quota state into a frontend-safe block reason.
- quota_payload: convert a quota row into JSON/admin display data.

Notes:
- The billing cycle starts on the 25th of each month.
- Database quota rows are durable monthly/admin state.
- Redis rolling-window counting remains in src.utils.map_tile_quota.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from datetime import timedelta

from src.db.models import AuthAuditEvent, MapTileBrowserBlock, MapTileMonthlyQuota, MapTileUsageSession
from src.utils.map_tile_quota import record_tile_delta
from src.utils.map_tile_quota import is_monthly_blocked
from src.utils.time import utc_now


MAP_TILE_PROVIDER = "esri"
FALLBACK_PROVIDER = "osm"
BILLING_CYCLE_START_DAY = 25
DEFAULT_USAGE_SESSION_KEY_BYTES = 24


def current_billing_month(now: datetime | None = None) -> str:
    """
    Return the current Esri billing-cycle month key.

    Input Args:
      now: optional current UTC datetime for deterministic tests.

    Output:
      Billing-cycle key in YYYY-MM format.

    Notes:
      The Esri billing cycle starts on the 25th. Dates from the 1st to 24th
      belong to the previous cycle-start month.
    """
    current_time = now or utc_now()
    if current_time.day >= BILLING_CYCLE_START_DAY:
        return f"{current_time.year:04d}-{current_time.month:02d}"

    if current_time.month == 1:
        return f"{current_time.year - 1:04d}-12"
    return f"{current_time.year:04d}-{current_time.month - 1:02d}"


def quota_defaults_from_config(config: dict) -> dict:
    """
    Build default monthly quota values from map-limit configuration.

    Input Args:
      config: dictionary containing map limit values.

    Output:
      Dictionary of default values for a new MapTileMonthlyQuota row.
    """
    monthly_limit = int(config.get("monthly_limit") or 0)
    warning_threshold = int(config.get("warning_threshold") or (int(monthly_limit * 0.8) if monthly_limit else 0))
    hard_stop_threshold = int(config.get("hard_stop_threshold") or monthly_limit)
    return {
        "monthly_limit": monthly_limit,
        "warning_threshold": warning_threshold,
        "hard_stop_threshold": hard_stop_threshold,
    }


def get_or_create_current_quota(session, quota_config: dict) -> MapTileMonthlyQuota:
    """
    Load or create the quota row for the active billing cycle.

    Input Args:
      session: active SQLAlchemy session.
      quota_config: dictionary of default monthly quota values.

    Output:
      MapTileMonthlyQuota row for the current 25th-to-25th billing cycle.
    """
    billing_month = current_billing_month()
    quota = (
        session.query(MapTileMonthlyQuota)
        .filter(
            MapTileMonthlyQuota.billing_month == billing_month,
            MapTileMonthlyQuota.provider == MAP_TILE_PROVIDER,
        )
        .first()
    )
    if quota is not None:
        return quota

    defaults = quota_defaults_from_config(quota_config)
    quota = MapTileMonthlyQuota(
        billing_month=billing_month,
        provider=MAP_TILE_PROVIDER,
        estimated_tiles_used=0,
        monthly_limit=defaults["monthly_limit"],
        warning_threshold=defaults["warning_threshold"],
        hard_stop_threshold=defaults["hard_stop_threshold"],
    )
    session.add(quota)
    session.flush()
    return quota


def generate_usage_session_key() -> str:
    """
    Generate a public usage session key.

    Input Args:
      None.

    Output:
      URL-safe token used to identify one browser/page map usage session.
    """
    return secrets.token_urlsafe(DEFAULT_USAGE_SESSION_KEY_BYTES)


def get_or_create_usage_session(
    session,
    browser_cookie_id: str,
    role: str,
    user_id: int | None,
    race_id: int | None,
    page_path: str,
    provider: str = MAP_TILE_PROVIDER,
    session_key: str | None = None,
    user_agent_hash: str | None = None,
    ip_hash: str | None = None,
) -> MapTileUsageSession:
    """
    Load or create a summarized browser/page map tile usage session.

    Input Args:
      session: active SQLAlchemy session.
      browser_cookie_id: anonymous browser cookie id.
      role: role snapshot: anonymous, rider, or admin.
      user_id: logged-in user id when available.
      race_id: race being viewed when available.
      page_path: browser page path that reported tile usage.
      provider: map tile provider for the report.
      session_key: optional existing usage session key from the browser.
      user_agent_hash: optional privacy-preserving user-agent hash.
      ip_hash: optional privacy-preserving IP hash.

    Output:
      MapTileUsageSession row.
    """
    if session_key:
        usage_session = (
            session.query(MapTileUsageSession)
            .filter(MapTileUsageSession.session_key == session_key)
            .first()
        )
        if usage_session is not None:
            usage_session.session_last_seen_at = utc_now()
            return usage_session

    usage_session = MapTileUsageSession(
        session_key=generate_usage_session_key(),
        browser_cookie_id=browser_cookie_id,
        user_id=user_id,
        role=role,
        race_id=race_id,
        billing_month=current_billing_month(),
        page_path=page_path or "/",
        provider=provider,
        estimated_tiles_loaded=0,
        user_agent_hash=user_agent_hash,
        ip_hash=ip_hash,
    )
    session.add(usage_session)
    session.flush()
    return usage_session


def update_quota_threshold_flags(quota: MapTileMonthlyQuota, now: datetime | None = None) -> None:
    """
    Update monthly warning and hard-stop state after usage changes.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.
    """
    current_time = now or utc_now()
    estimated_tiles = int(quota.estimated_tiles_used or 0)
    warning_threshold = int(quota.warning_threshold or 0)
    hard_stop_threshold = int(quota.hard_stop_threshold or 0)

    if warning_threshold > 0 and estimated_tiles >= warning_threshold and quota.warning_triggered_at is None:
        quota.warning_triggered_at = current_time

    if hard_stop_threshold > 0 and estimated_tiles >= hard_stop_threshold:
        quota.hard_stop_active = True
        if quota.hard_stop_triggered_at is None:
            quota.hard_stop_triggered_at = current_time

    quota.updated_at = current_time


def apply_tile_usage_delta(
    quota: MapTileMonthlyQuota,
    usage_session: MapTileUsageSession,
    tiles_delta: int | str,
    now: datetime | None = None,
) -> int:
    """
    Apply a tile delta and update monthly threshold flags.

    Input Args:
      quota: MapTileMonthlyQuota row.
      usage_session: MapTileUsageSession row.
      tiles_delta: newly reported tile delta.
      now: optional current UTC datetime for deterministic tests.

    Output:
      Normalised tile delta that was applied.
    """
    delta = record_tile_delta(quota, usage_session, tiles_delta, now=now)
    update_quota_threshold_flags(quota, now=now)
    return delta


def record_browser_block(
    session,
    browser_cookie_id: str,
    user_id: int | None,
    reason: str,
    tiles_at_block: int | None,
    timeout_minutes: int | str,
    now: datetime | None = None,
) -> MapTileBrowserBlock:
    """
    Record a browser block row for admin visibility.

    Input Args:
      session: active SQLAlchemy session.
      browser_cookie_id: anonymous browser cookie id.
      user_id: logged-in user id when available.
      reason: block reason such as browser_limit.
      tiles_at_block: rolling-window count when the block happened.
      timeout_minutes: expected release window in minutes.
      now: optional current UTC datetime for deterministic tests.

    Output:
      Existing or newly-created MapTileBrowserBlock row.
    """
    current_time = now or utc_now()
    try:
        timeout = int(timeout_minutes)
    except (TypeError, ValueError):
        timeout = 0

    existing = (
        session.query(MapTileBrowserBlock)
        .filter(
            MapTileBrowserBlock.browser_cookie_id == browser_cookie_id,
            MapTileBrowserBlock.reason == reason,
            MapTileBrowserBlock.released_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        existing.tiles_at_block = tiles_at_block
        existing.blocked_until = current_time + timedelta(minutes=max(timeout, 0))
        existing.updated_at = current_time
        return existing

    block = MapTileBrowserBlock(
        browser_cookie_id=browser_cookie_id,
        user_id=user_id,
        reason=reason,
        tiles_at_block=tiles_at_block,
        blocked_at=current_time,
        blocked_until=current_time + timedelta(minutes=max(timeout, 0)),
    )
    session.add(block)
    session.flush()
    return block


def release_browser_blocks(
    session,
    browser_cookie_id: str,
    released_by_user_id: int | None,
    release_reason: str,
    now: datetime | None = None,
) -> int:
    """
    Mark active browser block rows as released.

    Input Args:
      session: active SQLAlchemy session.
      browser_cookie_id: anonymous browser cookie id.
      released_by_user_id: admin user id performing the release.
      release_reason: admin-visible release reason.
      now: optional current UTC datetime for deterministic tests.

    Output:
      Number of block rows marked released.
    """
    current_time = now or utc_now()
    active_blocks = (
        session.query(MapTileBrowserBlock)
        .filter(
            MapTileBrowserBlock.browser_cookie_id == browser_cookie_id,
            MapTileBrowserBlock.released_at.is_(None),
        )
        .all()
    )
    for block in active_blocks:
        block.released_at = current_time
        block.released_by_user_id = released_by_user_id
        block.release_reason = release_reason
        block.updated_at = current_time
    return len(active_blocks)


def set_viewers_only_blocked(quota: MapTileMonthlyQuota, blocked: bool, now: datetime | None = None) -> None:
    """
    Set anonymous viewer satellite blocking state.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      blocked: desired viewers-only block state.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.
    """
    quota.viewers_only_blocked = bool(blocked)
    quota.updated_at = now or utc_now()


def set_global_hard_stop(quota: MapTileMonthlyQuota, active: bool, now: datetime | None = None) -> None:
    """
    Set or clear global hard-stop state.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      active: desired hard-stop state.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.
    """
    current_time = now or utc_now()
    quota.hard_stop_active = bool(active)
    if active and quota.hard_stop_triggered_at is None:
        quota.hard_stop_triggered_at = current_time
    quota.updated_at = current_time


def set_monthly_thresholds(
    quota: MapTileMonthlyQuota,
    monthly_limit: int | str,
    warning_threshold: int | str,
    hard_stop_threshold: int | str,
    now: datetime | None = None,
) -> None:
    """
    Manually update the active monthly quota limits/thresholds.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      monthly_limit: displayed/nominal monthly tile allowance.
      warning_threshold: usage level that triggers admin warning state.
      hard_stop_threshold: usage level that activates the hard stop.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.

    Notes:
      These values live in the active database row. They do not edit .env.
      Environment values only seed newly-created billing-cycle rows.
    """
    try:
        limit = int(monthly_limit)
        warning = int(warning_threshold)
        hard_stop = int(hard_stop_threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError("monthly thresholds must be non-negative integers") from exc

    if limit < 0 or warning < 0 or hard_stop < 0:
        raise ValueError("monthly thresholds must be non-negative integers")
    if limit > 0 and warning > limit:
        raise ValueError("warning_threshold cannot be greater than monthly_limit")
    if limit > 0 and hard_stop > limit:
        raise ValueError("hard_stop_threshold cannot be greater than monthly_limit")
    if warning > 0 and hard_stop > 0 and warning > hard_stop:
        raise ValueError("warning_threshold cannot be greater than hard_stop_threshold")

    current_time = now or utc_now()
    estimate = int(quota.estimated_tiles_used or 0)

    quota.monthly_limit = limit
    quota.warning_threshold = warning
    quota.hard_stop_threshold = hard_stop

    if warning <= 0 or estimate < warning:
        quota.warning_triggered_at = None
    if hard_stop <= 0 or estimate < hard_stop:
        quota.hard_stop_triggered_at = None
        quota.hard_stop_active = False

    update_quota_threshold_flags(quota, now=current_time)
    quota.updated_at = current_time


def set_monthly_tile_estimate(
    quota: MapTileMonthlyQuota,
    estimated_tiles_used: int | str,
    now: datetime | None = None,
) -> None:
    """
    Manually correct the app-estimated monthly Esri tile usage.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      estimated_tiles_used: corrected tile estimate from the Esri platform/admin.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.

    Notes:
      If the corrected estimate drops below warning/hard-stop thresholds, the
      matching automatic flags are cleared because the previous estimate was
      considered inaccurate. Manual global hard-stop controls can still be
      reapplied from the admin page.
    """
    try:
        estimate = int(estimated_tiles_used)
    except (TypeError, ValueError) as exc:
        raise ValueError("estimated_tiles_used must be a non-negative integer") from exc

    if estimate < 0:
        raise ValueError("estimated_tiles_used must be a non-negative integer")

    current_time = now or utc_now()
    warning_threshold = int(quota.warning_threshold or 0)
    hard_stop_threshold = int(quota.hard_stop_threshold or 0)

    quota.estimated_tiles_used = estimate
    if warning_threshold <= 0 or estimate < warning_threshold:
        quota.warning_triggered_at = None
    if hard_stop_threshold <= 0 or estimate < hard_stop_threshold:
        quota.hard_stop_triggered_at = None
        quota.hard_stop_active = False

    update_quota_threshold_flags(quota, now=current_time)
    quota.updated_at = current_time


def set_monthly_override(
    quota: MapTileMonthlyQuota,
    duration_minutes: int | str,
    reason: str | None = None,
    now: datetime | None = None,
) -> None:
    """
    Enable a temporary monthly hard-stop override.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      duration_minutes: override duration in minutes.
      reason: optional admin reason.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.
    """
    try:
        duration = int(duration_minutes)
    except (TypeError, ValueError) as exc:
        raise ValueError("duration_minutes must be a positive integer") from exc

    if duration <= 0:
        raise ValueError("duration_minutes must be greater than zero")

    current_time = now or utc_now()
    quota.override_active = True
    quota.override_until = current_time + timedelta(minutes=duration)
    quota.override_reason = (reason or "").strip() or None
    quota.updated_at = current_time


def clear_monthly_override(quota: MapTileMonthlyQuota, now: datetime | None = None) -> None:
    """
    Disable any active monthly hard-stop override.

    Input Args:
      quota: MapTileMonthlyQuota row to update.
      now: optional current UTC datetime for deterministic tests.

    Output:
      None. The caller is responsible for committing the session.
    """
    quota.override_active = False
    quota.override_until = None
    quota.override_reason = None
    quota.updated_at = now or utc_now()


def record_quota_audit_event(
    session,
    actor_user_id: int | None,
    action: str,
    metadata: dict | None = None,
) -> AuthAuditEvent:
    """
    Record an admin/system map quota action in auth_audit_events.

    Input Args:
      session: active SQLAlchemy session.
      actor_user_id: user id that performed the action, or None for system.
      action: short event name.
      metadata: optional safe JSON metadata.

    Output:
      AuthAuditEvent row.
    """
    event = AuthAuditEvent(
        actor_user_id=actor_user_id,
        target_user_id=None,
        action=action,
        metadata_json=json.dumps(metadata or {}, sort_keys=True),
    )
    session.add(event)
    session.flush()
    return event


def monthly_block_reason(quota: MapTileMonthlyQuota, role: str, is_admin: bool) -> str | None:
    """
    Convert monthly quota state into a frontend-safe block reason.

    Input Args:
      quota: current MapTileMonthlyQuota row.
      role: role snapshot for this browser.
      is_admin: whether the current browser is an admin.

    Output:
      Reason string when monthly/global quota blocks satellite; otherwise None.
    """
    if not is_monthly_blocked(quota, role=role, is_admin=is_admin):
        return None

    if quota.viewers_only_blocked and role == "anonymous":
        return "viewers_disabled"

    estimated_tiles = int(quota.estimated_tiles_used or 0)
    hard_stop_threshold = int(quota.hard_stop_threshold or 0)
    if quota.hard_stop_active or (hard_stop_threshold > 0 and estimated_tiles >= hard_stop_threshold):
        return "monthly_limit"

    return "monthly_blocked"


def quota_payload(quota: MapTileMonthlyQuota) -> dict:
    """
    Convert a monthly quota row into API/admin display data.

    Input Args:
      quota: MapTileMonthlyQuota row.

    Output:
      Dictionary safe to expose to the browser.
    """
    return {
        "billingMonth": quota.billing_month,
        "provider": quota.provider,
        "estimatedTilesUsed": int(quota.estimated_tiles_used or 0),
        "monthlyLimit": int(quota.monthly_limit or 0),
        "warningThreshold": int(quota.warning_threshold or 0),
        "hardStopThreshold": int(quota.hard_stop_threshold or 0),
        "hardStopActive": bool(quota.hard_stop_active),
        "viewersOnlyBlocked": bool(quota.viewers_only_blocked),
        "overrideActive": bool(quota.override_active),
        "overrideUntil": quota.override_until.isoformat() if quota.override_until else None,
    }
