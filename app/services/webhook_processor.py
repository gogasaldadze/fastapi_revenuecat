"""Applying a parsed webhook event to the database.

Users are matched by every id RevenueCat sends (app_user_id,
original_app_user_id, aliases) against users.rc_app_user_id. The webhook
never creates a binding. Only a verified /subscription/sync does that, so an
anonymous id that nobody has bound yet is skipped.

Each event is stored with a unique event_id before anything else happens.
A repeated delivery hits that constraint and is reported as a duplicate, so
processing is idempotent. Database errors propagate to the route, which
answers 503 so that RevenueCat delivers the event again.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.locks import set_lock_timeout
from app.db.models import SubscriptionEvent, User
from app.services.entitlement_cache import cache_bust
from app.services.subscription_state import grant, is_stale, lock_users, revoke
from app.services.webhook_events import ANONYMOUS_PREFIX, Bucket, WebhookEvent, classify

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    """What happened to one event."""

    bucket: Bucket
    duplicate: bool = False
    matched: int = 0
    stale: int = 0


def _record(event: WebhookEvent, bucket: Bucket) -> SubscriptionEvent:
    return SubscriptionEvent(
        event_id=event.id,
        event_type=event.type[:64],
        bucket=bucket.value,
        app_user_id=event.primary_id,
        product_id=event.product_id[:255] if event.product_id else None,
        store=event.store[:32] if event.store else None,
        environment=event.environment[:32],
        event_at=event.event_at,
        expiration_at=event.expires_at,
        payload=json.dumps(event.raw) if settings.store_webhook_payloads else None,
    )


async def _claim(db: AsyncSession, event: WebhookEvent, bucket: Bucket) -> bool:
    """Insert the event row. False if this event id was processed before."""
    db.add(_record(event, bucket))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return False
    return True


def _apply_to_matched(users: list[User], event: WebhookEvent, bucket: Bucket) -> tuple[int, int]:
    applied = stale = 0
    for user in users:
        if is_stale(user, event.event_at):
            stale += 1
            continue
        if bucket is Bucket.SYNC:
            grant(user, event.expires_at, event.event_at)
        else:
            revoke(user, event.event_at)
        applied += 1
    return applied, stale


async def _apply_transfer(db: AsyncSession, event: WebhookEvent) -> tuple[int, int]:
    """Move premium from users bound to transferred_from to users bound to transferred_to."""
    users = await lock_users(db, event.transferred_from + event.transferred_to)
    sources = [u for u in users if u.rc_app_user_id in event.transferred_from]
    targets = [u for u in users if u.rc_app_user_id in event.transferred_to]

    premium_sources = [u for u in sources if u.is_premium]
    lifetime = any(u.premium_expires_at is None for u in premium_sources)
    expiries = [u.premium_expires_at for u in premium_sources if u.premium_expires_at]
    best = None if lifetime or not expiries else max(expiries)

    applied = stale = 0
    for user in sources:
        if is_stale(user, event.event_at):
            stale += 1
            continue
        revoke(user, event.event_at)
        applied += 1
    if not premium_sources:
        # Nothing to move. A target user picks up access through a verified sync.
        return applied, stale
    for user in targets:
        if is_stale(user, event.event_at):
            stale += 1
            continue
        grant(user, best, event.event_at)
        applied += 1
    return applied, stale


async def process_event(db: AsyncSession, redis: Redis, event: WebhookEvent) -> ProcessResult:
    """Apply one webhook event and commit.

    Args:
        db: Session for the event row and user updates.
        redis: Used to bust cached entitlement checks for every id in the event.
        event: A parsed event.

    Raises:
        SQLAlchemyError: On database failures, including lock timeouts.
    """
    log_extra: dict[str, Any] = {
        "event_id": event.id,
        "event_type": event.type,
        "app_user_id": event.primary_id,
    }
    if event.environment == "SANDBOX" and not settings.sandbox_allowed:
        logger.info("Ignoring sandbox event", extra=log_extra)
        return ProcessResult(Bucket.SANDBOX_IGNORED)

    bucket = classify(event.type)
    await set_lock_timeout(db)
    if not await _claim(db, event, bucket):
        logger.info("Duplicate event, already processed", extra=log_extra)
        return ProcessResult(bucket, duplicate=True)

    matched = stale = 0
    if bucket in (Bucket.SYNC, Bucket.EXPIRE):
        users = await lock_users(db, event.app_user_ids)
        matched, stale = _apply_to_matched(users, event, bucket)
    elif bucket is Bucket.TRANSFER:
        matched, stale = await _apply_transfer(db, event)

    await db.commit()
    await cache_bust(redis, event.all_ids)

    if bucket is Bucket.UNKNOWN:
        logger.info("Unhandled RevenueCat event type %r", event.type, extra=log_extra)
    if bucket in (Bucket.SYNC, Bucket.EXPIRE) and not matched and not stale:
        kind = "anonymous" if (event.primary_id or "").startswith(ANONYMOUS_PREFIX) else "app"
        logger.info("No user bound to %s id, skipped", kind, extra=log_extra)
    if stale:
        logger.info("Stale event ignored for %d user(s)", stale, extra=log_extra)
    logger.info(
        "RevenueCat event processed",
        extra={**log_extra, "bucket": bucket.value, "matched": matched, "stale": stale},
    )
    return ProcessResult(bucket, matched=matched, stale=stale)
