"""Weighted daily quotas in Redis.

One counter per user per UTC day, increased by the feature weight. A request
that would go over the limit gets its units back, so a denied request does
not burn budget.
"""

import logging
from datetime import UTC, datetime

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

QUOTA_PREFIX = "quota"
# One UTC day plus some slack so the key outlives the day it counts.
QUOTA_TTL_SECONDS = 26 * 3600


class QuotaBackendError(Exception):
    """Redis failed. The caller decides whether to fail open or closed."""


def utc_day(now: datetime | None = None) -> str:
    """The UTC calendar day as YYYY-MM-DD."""
    return (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()


def quota_key(user_id: str, day: str) -> str:
    """Redis key for one user and one day."""
    return f"{QUOTA_PREFIX}:{user_id}:{day}"


async def consume_units(redis: Redis, user_id: str, units: int, limit: int) -> bool:
    """Charge `units` against today's counter.

    Returns:
        True if the charge fits under `limit`. False otherwise, and the units
        are refunded.

    Raises:
        QuotaBackendError: Redis is unreachable or failed.
    """
    key = quota_key(user_id, utc_day())
    try:
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incrby(key, units)
            pipe.expire(key, QUOTA_TTL_SECONDS)
            total, _ = await pipe.execute()
    except Exception as exc:
        raise QuotaBackendError(str(exc)) from exc

    if int(total) <= limit:
        return True

    try:
        await redis.decrby(key, units)
    except Exception as exc:
        logger.warning("Quota refund failed", extra={"user_id": user_id, "error": str(exc)})
    return False
