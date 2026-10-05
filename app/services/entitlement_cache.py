"""Redis cache for entitlement checks.

Cache failures are logged and ignored. A missing cache only costs an extra
request to RevenueCat.
"""

import json
import logging
from collections.abc import Iterable
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "rc_entitlement"


def cache_key(app_user_id: str) -> str:
    """Redis key for one RevenueCat app user id."""
    return f"{CACHE_PREFIX}:{app_user_id}"


async def cache_get(redis: Redis, app_user_id: str) -> dict[str, Any] | None:
    """Return the cached entry, or None on a miss, bad data or a Redis error."""
    try:
        raw = await redis.get(cache_key(app_user_id))
    except Exception as exc:
        logger.warning("Entitlement cache read failed", extra={"error": str(exc)})
        return None
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def cache_set(
    redis: Redis, app_user_id: str, value: dict[str, Any], ttl_seconds: int
) -> None:
    """Store an entry. Errors are logged, never raised."""
    try:
        await redis.set(cache_key(app_user_id), json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("Entitlement cache write failed", extra={"error": str(exc)})


async def cache_bust(redis: Redis, app_user_ids: Iterable[str]) -> None:
    """Drop cached entries so the next check asks RevenueCat."""
    keys = [cache_key(app_user_id) for app_user_id in app_user_ids]
    if not keys:
        return
    try:
        await redis.delete(*keys)
    except Exception as exc:
        logger.warning("Entitlement cache bust failed", extra={"error": str(exc)})
