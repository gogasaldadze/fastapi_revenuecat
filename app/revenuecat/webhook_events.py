"""RevenueCat webhook event processing.

Every event lands in one of five buckets:

    SYNC             premium on, expiry updated
    EXPIRE           premium off
    INFO_ONLY        recorded only, the user keeps access until expiry
    UNKNOWN          recorded only
    SANDBOX_IGNORED  dropped, unless sandbox purchases are allowed

Users are matched by every id RevenueCat sends (app_user_id,
original_app_user_id, aliases) against users.rc_app_user_id. The webhook
never creates a binding. Only a verified /subscription/sync does that, so an
anonymous id that nobody has bound yet is skipped.
"""

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import APP_USER_ID_MAX_LENGTH, SubscriptionEvent, User
from app.revenuecat.cache import cache_bust

logger = logging.getLogger(__name__)

ANONYMOUS_PREFIX = "$RCAnonymousID:"


class Bucket(StrEnum):
    SYNC = "sync"
    EXPIRE = "expire"
    INFO_ONLY = "info_only"
    UNKNOWN = "unknown"
    SANDBOX_IGNORED = "sandbox_ignored"


SYNC_EVENTS = frozenset({"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"})
EXPIRE_EVENTS = frozenset({"EXPIRATION", "SUBSCRIPTION_PAUSED"})
INFO_ONLY_EVENTS = frozenset({"CANCELLATION", "BILLING_ISSUE"})


def classify(event_type: str) -> Bucket:
    name = (event_type or "").upper()
    if name in SYNC_EVENTS:
        return Bucket.SYNC
    if name in EXPIRE_EVENTS:
        return Bucket.EXPIRE
    if name in INFO_ONLY_EVENTS:
        return Bucket.INFO_ONLY
    return Bucket.UNKNOWN


def event_user_ids(event: dict) -> tuple[str, ...]:
    """app_user_id, original_app_user_id and aliases, deduplicated in order."""
    aliases = event.get("aliases")
    raw = [
        event.get("app_user_id"),
        event.get("original_app_user_id"),
        *(aliases if isinstance(aliases, list) else []),
    ]
    valid = (r for r in raw if isinstance(r, str) and 0 < len(r) <= APP_USER_ID_MAX_LENGTH)
    return tuple(dict.fromkeys(valid))


def parse_ms(raw) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC) if raw else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _clip(value, length: int) -> str | None:
    return str(value)[:length] if value is not None else None


def _record(event: dict, bucket: Bucket, primary_id: str | None) -> SubscriptionEvent:
    return SubscriptionEvent(
        event_id=_clip(event.get("id"), 64),
        event_type=_clip(event.get("type") or "UNKNOWN", 64),
        bucket=bucket.value,
        app_user_id=primary_id,
        product_id=_clip(event.get("product_id"), 255),
        store=_clip(event.get("store"), 32),
        environment=_clip(event.get("environment"), 32),
        expiration_at=parse_ms(event.get("expiration_at_ms")),
        payload=json.dumps(event),
    )


async def _apply(db: AsyncSession, bucket: Bucket, ids: tuple[str, ...], event: dict) -> int:
    if bucket is Bucket.SYNC:
        # A null expiration means a lifetime purchase.
        values = {"is_premium": True, "premium_expires_at": parse_ms(event.get("expiration_at_ms"))}
    else:
        values = {"is_premium": False}
    result = await db.execute(
        update(User)
        .where(User.rc_app_user_id.in_(ids))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def process_event(db: AsyncSession, redis: Redis, event: dict) -> Bucket:
    event_type = str(event.get("type") or "")
    environment = str(event.get("environment") or "PRODUCTION").upper()
    ids = event_user_ids(event)
    primary_id = ids[0] if ids else None

    if environment == "SANDBOX" and not settings.sandbox_allowed:
        logger.info("Ignoring sandbox event %s for %s", event_type, primary_id)
        return Bucket.SANDBOX_IGNORED

    bucket = classify(event_type)
    if settings.store_webhook_events:
        db.add(_record(event, bucket, primary_id))

    matched = 0
    if ids and bucket in (Bucket.SYNC, Bucket.EXPIRE):
        matched = await _apply(db, bucket, ids, event)

    await db.commit()
    await cache_bust(redis, ids)

    if bucket is Bucket.UNKNOWN:
        logger.info("Unhandled RevenueCat event type %r", event_type)
    if bucket in (Bucket.SYNC, Bucket.EXPIRE) and not matched:
        kind = "anonymous" if (primary_id or "").startswith(ANONYMOUS_PREFIX) else "app"
        logger.info("%s: no user bound to %s id %s, skipped", event_type, kind, primary_id)
    logger.info(
        "RevenueCat event type=%s bucket=%s user=%s matched=%d",
        event_type,
        bucket.value,
        primary_id,
        matched,
    )
    return bucket
