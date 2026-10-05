"""Redis cache for entitlement checks.

Cache failures are logged and ignored. A missing cache only means an extra
request to RevenueCat.
"""

import json
import logging
from collections.abc import Iterable

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "rc_entitlement"


def cache_key(app_user_id: str) -> str:
    return f"{CACHE_PREFIX}:{app_user_id}"


async def cache_get(redis: Redis, app_user_id: str) -> dict | None:
    try:
        raw = await redis.get(cache_key(app_user_id))
    except Exception as exc:
        logger.warning("Entitlement cache read failed: %s", exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def cache_set(redis: Redis, app_user_id: str, value: dict, ttl_seconds: int) -> None:
    try:
        await redis.set(cache_key(app_user_id), json.dumps(value), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("Entitlement cache write failed: %s", exc)


async def cache_bust(redis: Redis, app_user_ids: Iterable[str]) -> None:
    keys = [cache_key(app_user_id) for app_user_id in app_user_ids]
    if not keys:
        return
    try:
        await redis.delete(*keys)
    except Exception as exc:
        logger.warning("Entitlement cache bust failed: %s", exc)
