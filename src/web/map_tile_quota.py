"""
Map tile quota admin and browser configuration routes.

These routes control whether the browser may receive Esri satellite map
configuration. They do not proxy Esri tiles. The frontend still loads map tiles
directly from Esri when allowed, while this module decides when to release the
browser-facing Esri API key and when to fall back to OpenStreetMap.

Routes
------
GET /admin/map_tile_quota -> Admin-only page showing current quota/block state
GET /api/map/config-status -> Public browser endpoint returning Esri/fallback config
POST /api/map/tile-usage -> Browser tile delta reporting and quota enforcement
POST /admin/map_tile_quota/browser/<browser_cookie_id>/reset -> Admin browser reset
POST /admin/map_tile_quota/global-toggle -> Admin global/viewer satellite toggle
POST /admin/map_tile_quota/monthly-override -> Admin monthly hard-stop override
POST /admin/map_tile_quota/monthly-override/clear -> Admin override cancellation

Notes
-----
* Admin routes must use admin_required.
* Public map config routes do not require login because anonymous viewers need
  read-only race maps.
* Billing-cycle and quota DB business rules live in src.services.map_tile_quota.
* Browser cookie, Redis key, and rolling-window helpers live in
  src.utils.map_tile_quota.
* Tile-provider cost control is called out in the system design risk section;
  this module is part of that control layer.

Contains:
- _config_int: read integer values from Flask configuration.
- _map_quota_config: gather map quota defaults from Flask configuration.
- _get_redis_client: get/create the Flask-cached Redis client.
- _current_browser_role: convert Flask-Login state into a quota role snapshot.
- _current_user_id: return the logged-in user id when available.
- _safe_int: parse optional integer request values.
- admin_map_tile_quota: render the admin map tile quota page.
- map_config_status: return Esri/fallback map config status as JSON.
- map_tile_usage: record browser tile deltas and enforce quota state.
- reset_browser_quota: reset one browser's current rolling-window usage.
- global_toggle: update viewer/global block flags.
- monthly_override: set a temporary monthly quota override.
- clear_monthly_override_route: clear the monthly quota override.
"""

from __future__ import annotations

import hashlib

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from src.auth.decorators import admin_required
from src.auth.rate_limits import limiter
from src.db.models import MapTileBrowserBlock, SessionLocal
from src.services.map_tile_quota import (
    BILLING_CYCLE_START_DAY,
    FALLBACK_PROVIDER,
    MAP_TILE_PROVIDER,
    apply_tile_usage_delta,
    clear_monthly_override,
    get_or_create_current_quota,
    get_or_create_usage_session,
    monthly_block_reason,
    quota_payload as build_quota_payload,
    record_browser_block,
    record_quota_audit_event,
    release_browser_blocks,
    set_global_hard_stop,
    set_monthly_override,
    set_viewers_only_blocked,
)
from src.utils.map_tile_quota import (
    get_or_create_browser_cookie_id,
    get_browser_tile_count,
    increment_browser_tile_count,
    is_browser_blocked,
    is_browser_over_tile_limit,
    reset_browser_block,
)


bp_map_tile_quota = Blueprint("map_tile_quota", __name__)


def _config_int(name: str, default: int = 0) -> int:
    """
    Read an integer value from Flask configuration.

    Input Args:
      name: Flask config key to read.
      default: fallback integer when the value is missing or invalid.

    Output:
      Parsed integer value or the supplied default.
    """
    try:
        return int((current_app.config.get(name) or "").strip())
    except (AttributeError, TypeError, ValueError):
        return default


def _map_quota_config() -> dict:
    """
    Gather map quota defaults from Flask configuration.

    Input Args:
      None. Reads Flask app config.

    Output:
      Dictionary containing monthly_limit, warning_threshold, and
      hard_stop_threshold values for the service layer.
    """
    monthly_limit = _config_int("MAP_TILE_MONTHLY_LIMIT", 0)
    warning_threshold = _config_int("MAP_TILE_WARNING_THRESHOLD", int(monthly_limit * 0.8) if monthly_limit else 0)
    hard_stop_threshold = _config_int("MAP_TILE_HARD_STOP_THRESHOLD", monthly_limit)
    return {
        "monthly_limit": monthly_limit,
        "warning_threshold": warning_threshold,
        "hard_stop_threshold": hard_stop_threshold,
    }


def _get_redis_client():
    """
    Get or create the Redis client used for map tile quota checks.

    Input Args:
      None. Reads Flask app config.

    Output:
      Redis client connected to AUTH_RATE_LIMIT_STORAGE_URL.

    Raises:
      RuntimeError when Redis configuration is missing.
    """
    redis_url = (current_app.config.get("AUTH_RATE_LIMIT_STORAGE_URL") or "").strip()
    if not redis_url:
        raise RuntimeError("AUTH_RATE_LIMIT_STORAGE_URL is required for map tile quota checks.")

    redis_client = current_app.extensions.get("map_tile_quota_redis")
    if redis_client is None:
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        current_app.extensions["map_tile_quota_redis"] = redis_client
    return redis_client


def _current_browser_role() -> tuple[str, bool]:
    """
    Return the current browser role snapshot.

    Input Args:
      None. Reads Flask-Login current_user.

    Output:
      Tuple of role string and admin boolean.
    """
    if getattr(current_user, "is_authenticated", False):
        role = str(getattr(current_user, "role", "") or "").strip().lower()
        if role in {"rider", "admin"}:
            return role, role == "admin"
    return "anonymous", False


def _current_user_id() -> int | None:
    """
    Return the current authenticated user id when available.

    Input Args:
      None. Reads Flask-Login current_user.

    Output:
      Integer user id, or None for anonymous viewers.
    """
    if getattr(current_user, "is_authenticated", False):
        return getattr(current_user, "id", None)
    return None


def _safe_int(value, default: int | None = None) -> int | None:
    """
    Parse optional integer request values.

    Input Args:
      value: raw submitted value.
      default: fallback value when parsing fails.

    Output:
      Parsed integer or default.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hash_request_value(value: str | None) -> str | None:
    """
    Hash request metadata before storing it in usage analytics.

    Input Args:
      value: raw request metadata value such as user-agent or IP.

    Output:
      SHA-256 hex digest, or None when value is missing.
    """
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


@bp_map_tile_quota.route("/admin/map_tile_quota")
@admin_required
def admin_map_tile_quota():
    """
    Render the admin map tile quota management page.
    """
    session = SessionLocal()
    try:
        quota = get_or_create_current_quota(session, _map_quota_config())
        active_blocks = (
            session.query(MapTileBrowserBlock)
            .filter(MapTileBrowserBlock.released_at.is_(None))
            .order_by(MapTileBrowserBlock.blocked_at.desc())
            .limit(50)
            .all()
        )
        session.commit()
        redis_status = "configured"
        try:
            _get_redis_client().ping()
            redis_status = "connected"
        except (RedisError, RuntimeError):
            redis_status = "unavailable"

        return render_template(
            "map_tile_quota.html",
            quota=quota,
            quota_payload=build_quota_payload(quota),
            active_blocks=active_blocks,
            redis_status=redis_status,
            billing_cycle_start_day=BILLING_CYCLE_START_DAY,
        )
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


@bp_map_tile_quota.route("/api/map/config-status")
def map_config_status():
    """
    Return browser map-provider configuration when satellite imagery is allowed.
    """
    response = make_response()
    browser_cookie_id = get_or_create_browser_cookie_id(
        request,
        response,
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", True)),
        same_site=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
    )

    role, is_admin = _current_browser_role()
    provider = (current_app.config.get("MAP_PROVIDER") or "").strip().lower()
    map_style = (current_app.config.get("MAP_STYLE") or "").strip() or "arcgis/imagery"
    arcgis_api_key = (current_app.config.get("ARCGIS_API_KEY") or "").strip()
    user_limit = _config_int("MAP_TILE_USER_LIMIT", 0)
    timeout_minutes = _config_int("MAP_USER_LIMIT_TIMEOUT_MIN", 30)
    reason = None
    quota_payload = None
    rolling_browser_tiles = None

    session = SessionLocal()
    try:
        quota = get_or_create_current_quota(session, _map_quota_config())
        session.commit()
        quota_payload = build_quota_payload(quota)

        if provider != MAP_TILE_PROVIDER:
            reason = "provider_not_esri"
        elif not arcgis_api_key:
            reason = "missing_arcgis_api_key"
        else:
            try:
                redis_client = _get_redis_client()
                rolling_browser_tiles = get_browser_tile_count(
                    redis_client,
                    browser_cookie_id,
                    timeout_minutes=timeout_minutes,
                )
                if is_browser_blocked(redis_client, browser_cookie_id):
                    reason = "browser_limit"
                elif (
                    not is_admin
                    and user_limit > 0
                    and is_browser_over_tile_limit(
                        redis_client,
                        browser_cookie_id,
                        tile_limit=user_limit,
                        timeout_minutes=timeout_minutes,
                    )
                ):
                    reason = "browser_limit"
            except (RedisError, RuntimeError):
                reason = "quota_store_unavailable"

            if reason is None:
                reason = monthly_block_reason(quota, role=role, is_admin=is_admin)
    except SQLAlchemyError:
        session.rollback()
        reason = "quota_store_unavailable"
    finally:
        session.close()

    satellite_allowed = reason is None
    payload = {
        "satelliteAllowed": satellite_allowed,
        "provider": MAP_TILE_PROVIDER if satellite_allowed else FALLBACK_PROVIDER,
        "fallbackProvider": FALLBACK_PROVIDER,
        "reason": reason,
        "role": role,
        "quota": quota_payload,
        "rollingBrowserTiles": rolling_browser_tiles,
    }

    if satellite_allowed:
        payload.update(
            {
                "mapProvider": MAP_TILE_PROVIDER,
                "mapStyle": map_style,
                "arcgisApiKey": arcgis_api_key,
            }
        )

    response.set_data(jsonify(payload).get_data())
    response.mimetype = "application/json"
    return response


@bp_map_tile_quota.route("/api/map/tile-usage", methods=["POST"])
@limiter.limit("120 per minute")
def map_tile_usage():
    """
    Record browser tile usage deltas and return updated quota status.
    """
    response = make_response()
    browser_cookie_id = get_or_create_browser_cookie_id(
        request,
        response,
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE", True)),
        same_site=current_app.config.get("SESSION_COOKIE_SAMESITE", "Lax"),
    )

    data = request.get_json(silent=True) or {}
    tiles_delta = _safe_int(data.get("tiles_delta"), 0) or 0
    if tiles_delta < 0:
        response.status_code = 400
        response.set_data(jsonify({"error": "tiles_delta must be a non-negative integer"}).get_data())
        response.mimetype = "application/json"
        return response

    role, is_admin = _current_browser_role()
    user_id = _current_user_id()
    user_limit = _config_int("MAP_TILE_USER_LIMIT", 0)
    timeout_minutes = _config_int("MAP_USER_LIMIT_TIMEOUT_MIN", 30)
    rolling_browser_tiles = None
    reason = None
    quota_payload = None
    usage_session_key = data.get("session_key")

    try:
        redis_client = _get_redis_client()
        rolling_browser_tiles = increment_browser_tile_count(
            redis_client,
            browser_cookie_id,
            tiles_delta,
            timeout_minutes=timeout_minutes,
        )
        if (
            not is_admin
            and user_limit > 0
            and is_browser_over_tile_limit(
                redis_client,
                browser_cookie_id,
                tile_limit=user_limit,
                timeout_minutes=timeout_minutes,
            )
        ):
            reason = "browser_limit"
    except (RedisError, RuntimeError):
        reason = "quota_store_unavailable"

    session = SessionLocal()
    try:
        quota = get_or_create_current_quota(session, _map_quota_config())
        usage_session = get_or_create_usage_session(
            session,
            browser_cookie_id=browser_cookie_id,
            role=role,
            user_id=user_id,
            race_id=_safe_int(data.get("race_id")),
            page_path=(data.get("page_path") or request.referrer or "/")[:512],
            provider=MAP_TILE_PROVIDER,
            session_key=usage_session_key,
            user_agent_hash=_hash_request_value(request.headers.get("User-Agent")),
            ip_hash=_hash_request_value(request.remote_addr),
        )
        usage_session_key = usage_session.session_key

        if reason != "quota_store_unavailable":
            apply_tile_usage_delta(quota, usage_session, tiles_delta)
            monthly_reason = monthly_block_reason(quota, role=role, is_admin=is_admin)
            if monthly_reason is not None:
                reason = monthly_reason

            if reason == "browser_limit":
                usage_session.blocked_reason = reason
                record_browser_block(
                    session,
                    browser_cookie_id=browser_cookie_id,
                    user_id=user_id,
                    reason=reason,
                    tiles_at_block=rolling_browser_tiles,
                    timeout_minutes=timeout_minutes,
                )

        quota_payload = build_quota_payload(quota)
        session.commit()
    except (SQLAlchemyError, ValueError):
        session.rollback()
        reason = "quota_store_unavailable"
    finally:
        session.close()

    satellite_allowed = reason is None
    payload = {
        "satelliteAllowed": satellite_allowed,
        "provider": MAP_TILE_PROVIDER if satellite_allowed else FALLBACK_PROVIDER,
        "fallbackProvider": FALLBACK_PROVIDER,
        "reason": reason,
        "role": role,
        "usageSessionKey": usage_session_key,
        "rollingBrowserTiles": rolling_browser_tiles,
        "quota": quota_payload,
    }
    response.set_data(jsonify(payload).get_data())
    response.mimetype = "application/json"
    return response


@bp_map_tile_quota.route("/admin/map_tile_quota/browser/<browser_cookie_id>/reset", methods=["POST"])
@admin_required
def reset_browser_quota(browser_cookie_id: str):
    """
    Reset one browser's current map tile quota block/count state.
    """
    timeout_minutes = _config_int("MAP_USER_LIMIT_TIMEOUT_MIN", 30)
    try:
        reset_browser_block(
            _get_redis_client(),
            browser_cookie_id,
            reset_count=True,
            timeout_minutes=timeout_minutes,
        )
    except (RedisError, RuntimeError):
        pass

    session = SessionLocal()
    try:
        released_count = release_browser_blocks(
            session,
            browser_cookie_id=browser_cookie_id,
            released_by_user_id=_current_user_id(),
            release_reason="admin_reset",
        )
        record_quota_audit_event(
            session,
            actor_user_id=_current_user_id(),
            action="map_tile_browser_reset",
            metadata={"browser_cookie_id": browser_cookie_id, "released_count": released_count},
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()
    return redirect(url_for("map_tile_quota.admin_map_tile_quota"))


@bp_map_tile_quota.route("/admin/map_tile_quota/global-toggle", methods=["POST"])
@admin_required
def global_toggle():
    """
    Update global/viewer satellite access flags.
    """
    viewers_blocked = request.form.get("viewers_only_blocked") == "1"
    hard_stop_active = request.form.get("hard_stop_active") == "1"

    session = SessionLocal()
    try:
        quota = get_or_create_current_quota(session, _map_quota_config())
        set_viewers_only_blocked(quota, viewers_blocked)
        set_global_hard_stop(quota, hard_stop_active)
        record_quota_audit_event(
            session,
            actor_user_id=_current_user_id(),
            action="map_tile_global_toggle",
            metadata={
                "viewers_only_blocked": viewers_blocked,
                "hard_stop_active": hard_stop_active,
                "billing_month": quota.billing_month,
            },
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()
    return redirect(url_for("map_tile_quota.admin_map_tile_quota"))


@bp_map_tile_quota.route("/admin/map_tile_quota/monthly-override", methods=["POST"])
@admin_required
def monthly_override():
    """
    Enable a temporary monthly hard-stop override.
    """
    duration_minutes = request.form.get("duration_minutes")
    reason = request.form.get("reason")

    session = SessionLocal()
    try:
        quota = get_or_create_current_quota(session, _map_quota_config())
        set_monthly_override(quota, duration_minutes=duration_minutes, reason=reason)
        record_quota_audit_event(
            session,
            actor_user_id=_current_user_id(),
            action="map_tile_monthly_override_set",
            metadata={
                "duration_minutes": duration_minutes,
                "reason": (reason or "").strip(),
                "billing_month": quota.billing_month,
            },
        )
        session.commit()
    except (SQLAlchemyError, ValueError):
        session.rollback()
        raise
    finally:
        session.close()
    return redirect(url_for("map_tile_quota.admin_map_tile_quota"))


@bp_map_tile_quota.route("/admin/map_tile_quota/monthly-override/clear", methods=["POST"])
@admin_required
def clear_monthly_override_route():
    """
    Clear the current monthly hard-stop override.
    """
    session = SessionLocal()
    try:
        quota = get_or_create_current_quota(session, _map_quota_config())
        clear_monthly_override(quota)
        record_quota_audit_event(
            session,
            actor_user_id=_current_user_id(),
            action="map_tile_monthly_override_clear",
            metadata={"billing_month": quota.billing_month},
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()
    return redirect(url_for("map_tile_quota.admin_map_tile_quota"))
