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
from urllib.parse import quote

import httpx
from redis.asyncio import Redis

from app.config import settings
from app.revenuecat.cache import cache_bust, cache_get, cache_set
from app.timeutil import as_utc, utcnow

logger = logging.getLogger(__name__)


class EntitlementStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Entitlement:
    status: EntitlementStatus
    # None with ACTIVE means a lifetime purchase.
    expires_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.status is EntitlementStatus.ACTIVE


INACTIVE = Entitlement(EntitlementStatus.INACTIVE)
NOT_FOUND = Entitlement(EntitlementStatus.NOT_FOUND)
UNKNOWN = Entitlement(EntitlementStatus.UNKNOWN)


def parse_rc_date(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def is_sandbox_product(subscriber: dict, product_id: str | None) -> bool:
    if not product_id:
        return False
    subscription = (subscriber.get("subscriptions") or {}).get(product_id)
    if subscription is not None:
        return bool(subscription.get("is_sandbox"))
    purchases = (subscriber.get("non_subscriptions") or {}).get(product_id) or []
    return bool(purchases) and all(p.get("is_sandbox") for p in purchases)


def read_entitlement(
    subscriber: dict, entitlement_id: str, now: datetime, *, allow_sandbox: bool
) -> Entitlement:
    """Decide whether `entitlement_id` is active in a v1 subscriber object."""
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
    if not settings.revenuecat_secret_key:
        logger.warning("REVENUECAT_SECRET_KEY not set, cannot verify %s", app_user_id)
        return UNKNOWN

    url = f"{settings.revenuecat_api_url}/subscribers/{quote(app_user_id, safe='')}"
    try:
        resp = await http.get(
            url, headers={"Authorization": f"Bearer {settings.revenuecat_secret_key}"}
        )
    except httpx.HTTPError as exc:
        logger.warning("RevenueCat request failed for %s: %s", app_user_id, exc)
        return UNKNOWN

    if resp.status_code == 404:
        return NOT_FOUND
    if resp.status_code != 200:
        logger.warning("RevenueCat returned %s for %s", resp.status_code, app_user_id)
        return UNKNOWN

    try:
        subscriber = resp.json().get("subscriber") or {}
    except (ValueError, AttributeError):
        logger.warning("RevenueCat returned an unreadable body for %s", app_user_id)
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


def _to_cache(entitlement: Entitlement) -> dict:
    expires = entitlement.expires_at.isoformat() if entitlement.expires_at else None
    return {"status": entitlement.status.value, "expires_at": expires}


def _from_cache(value: dict) -> Entitlement | None:
    try:
        status = EntitlementStatus(value.get("status"))
    except ValueError:
        return None
    if status is EntitlementStatus.UNKNOWN:
        return None
    return Entitlement(status, parse_rc_date(value.get("expires_at")))


async def check_entitlement(
    http: httpx.AsyncClient, redis: Redis, app_user_id: str, *, fresh: bool = False
) -> Entitlement:
    """Cached lookup. `fresh` skips the cache, used right after a purchase."""
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
