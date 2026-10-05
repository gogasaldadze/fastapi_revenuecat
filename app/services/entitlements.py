"""Entitlement checks against the RevenueCat REST API, v1 GET /subscribers/{id}.

The v1 endpoint works with a plain secret key. How responses map to a status:

    200, entitlement active        ACTIVE
    200, missing or expired        INACTIVE
    404                            NOT_FOUND, fail closed
    5xx, 401, network, no key      UNKNOWN, the caller keeps prior state

UNKNOWN is never cached, so the next request tries RevenueCat again.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import httpx
from redis.asyncio import Redis

from app.core.config import settings
from app.core.timeutil import as_utc, utcnow
from app.services.entitlement_cache import cache_bust, cache_get, cache_set

logger = logging.getLogger(__name__)


class EntitlementStatus(StrEnum):
    """Outcome of an entitlement check."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Entitlement:
    """Status plus expiry. ACTIVE with no expiry means a lifetime purchase."""

    status: EntitlementStatus
    expires_at: datetime | None = None

    @property
    def active(self) -> bool:
        """True when the entitlement grants access right now."""
        return self.status is EntitlementStatus.ACTIVE


INACTIVE = Entitlement(EntitlementStatus.INACTIVE)
NOT_FOUND = Entitlement(EntitlementStatus.NOT_FOUND)
UNKNOWN = Entitlement(EntitlementStatus.UNKNOWN)


def parse_rc_date(value: object) -> datetime | None:
    """Parse an ISO 8601 date from RevenueCat. Returns None for anything else."""
    if not value or not isinstance(value, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def is_sandbox_product(subscriber: dict[str, Any], product_id: str | None) -> bool:
    """True when the purchase behind `product_id` was made in the sandbox."""
    if not product_id:
        return False
    subscription = (subscriber.get("subscriptions") or {}).get(product_id)
    if subscription is not None:
        return bool(subscription.get("is_sandbox"))
    purchases = (subscriber.get("non_subscriptions") or {}).get(product_id) or []
    return bool(purchases) and all(p.get("is_sandbox") for p in purchases)


def read_entitlement(
    subscriber: dict[str, Any], entitlement_id: str, now: datetime, *, allow_sandbox: bool
) -> Entitlement:
    """Decide whether an entitlement is active in a v1 subscriber object.

    Args:
        subscriber: The `subscriber` object from GET /v1/subscribers/{id}.
        entitlement_id: Entitlement identifier from the RevenueCat dashboard.
        now: Current time, passed in to keep this function pure.
        allow_sandbox: Whether sandbox purchases may grant access.

    Returns:
        ACTIVE with the later of the expiry and grace period dates, or INACTIVE.
    """
    entitlement = (subscriber.get("entitlements") or {}).get(entitlement_id)
    if not entitlement:
        return INACTIVE
    if not allow_sandbox and is_sandbox_product(subscriber, entitlement.get("product_identifier")):
        return INACTIVE

    if entitlement.get("expires_date") is None:
        return Entitlement(EntitlementStatus.ACTIVE)

    candidates = [
        parse_rc_date(entitlement.get("expires_date")),
        parse_rc_date(entitlement.get("grace_period_expires_date")),
    ]
    latest = max((d for d in candidates if d is not None), default=None)
    if latest is not None and latest > now:
        return Entitlement(EntitlementStatus.ACTIVE, latest)
    return INACTIVE


async def fetch_entitlement(http: httpx.AsyncClient, app_user_id: str) -> Entitlement:
    """Ask RevenueCat directly, without the cache."""
    log_extra = {"app_user_id": app_user_id}
    if not settings.revenuecat_secret_key:
        logger.warning("REVENUECAT_SECRET_KEY not set, cannot verify", extra=log_extra)
        return UNKNOWN

    url = f"{settings.revenuecat_api_url}/subscribers/{quote(app_user_id, safe='')}"
    try:
        resp = await http.get(
            url, headers={"Authorization": f"Bearer {settings.revenuecat_secret_key}"}
        )
    except httpx.HTTPError as exc:
        logger.warning("RevenueCat request failed", extra={**log_extra, "error": str(exc)})
        return UNKNOWN

    if resp.status_code == 404:
        return NOT_FOUND
    if resp.status_code != 200:
        logger.warning(
            "RevenueCat returned an error", extra={**log_extra, "status": resp.status_code}
        )
        return UNKNOWN

    try:
        subscriber = resp.json().get("subscriber") or {}
    except (ValueError, AttributeError):
        logger.warning("RevenueCat returned an unreadable body", extra=log_extra)
        return UNKNOWN

    return read_entitlement(
        subscriber,
        settings.revenuecat_entitlement_id,
        utcnow(),
        allow_sandbox=settings.sandbox_allowed,
    )


def _cache_ttl(entitlement: Entitlement) -> int:
    ttl = settings.entitlement_cache_ttl_seconds
    if entitlement.active and entitlement.expires_at is not None:
        # Never serve a cached ACTIVE past the moment it expires.
        remaining = int((entitlement.expires_at - utcnow()).total_seconds())
        ttl = min(ttl, max(remaining, 1))
    return ttl


def _to_cache(entitlement: Entitlement) -> dict[str, Any]:
    expires = entitlement.expires_at.isoformat() if entitlement.expires_at else None
    return {"status": entitlement.status.value, "expires_at": expires}


def _from_cache(value: dict[str, Any]) -> Entitlement | None:
    try:
        status = EntitlementStatus(str(value.get("status")))
    except ValueError:
        return None
    if status is EntitlementStatus.UNKNOWN:
        return None
    return Entitlement(status, parse_rc_date(value.get("expires_at")))


async def check_entitlement(
    http: httpx.AsyncClient, redis: Redis, app_user_id: str, *, fresh: bool = False
) -> Entitlement:
    """Cached entitlement lookup.

    Args:
        http: Shared HTTP client.
        redis: Redis client for the cache.
        app_user_id: RevenueCat app user id to check.
        fresh: Skip and replace the cached value. Use right after a purchase.
    """
    if fresh:
        await cache_bust(redis, [app_user_id])
    else:
        cached = await cache_get(redis, app_user_id)
        hit = _from_cache(cached) if cached else None
        if hit is not None:
            return hit

    entitlement = await fetch_entitlement(http, app_user_id)
    if entitlement.status is not EntitlementStatus.UNKNOWN:
        await cache_set(redis, app_user_id, _to_cache(entitlement), _cache_ttl(entitlement))
    return entitlement
