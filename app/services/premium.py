"""Server side premium status and RevenueCat id binding.

Premium always comes from the user's row, never from anything the client
sends on a normal request:

* Row has rc_app_user_id: ask RevenueCat (cached). If RevenueCat cannot be
  reached, fall back to the row, so an outage only keeps users premium who
  were already premium and not yet expired.
* Row has no rc_app_user_id: use the row (for accounts you grant by hand).

An rc_app_user_id is bound to a user only after RevenueCat confirms an
active entitlement for it.
"""

import logging
from datetime import datetime
from typing import Any, cast

import httpx
from redis.asyncio import Redis
from sqlalchemy import CursorResult, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.timeutil import as_utc, utcnow
from app.db.locks import set_lock_timeout
from app.db.models import APP_USER_ID_MAX_LENGTH, User
from app.services.entitlements import EntitlementStatus, check_entitlement
from app.services.subscription_state import grant

logger = logging.getLogger(__name__)

PLACEHOLDER_IDS = frozenset({"anonymous", "null", "nil", "none", "undefined"})


def normalize_app_user_id(raw: str | None) -> str | None:
    """Return a usable id, or None for empty, placeholder or malformed input."""
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > APP_USER_ID_MAX_LENGTH:
        return None
    if candidate.lower() in PLACEHOLDER_IDS or not candidate.isprintable():
        return None
    return candidate


def stored_premium(user: User) -> bool:
    """Premium according to the row alone, respecting the stored expiry."""
    if not user.is_premium:
        return False
    expires = user.premium_expires_at
    return expires is None or as_utc(expires) > utcnow()


async def premium_for_user(http: httpx.AsyncClient, redis: Redis, user: User) -> bool:
    """Decide whether `user` is premium right now. See the module docstring."""
    if not user.rc_app_user_id:
        return stored_premium(user)
    entitlement = await check_entitlement(http, redis, user.rc_app_user_id)
    if entitlement.status is EntitlementStatus.UNKNOWN:
        return stored_premium(user)
    return entitlement.active


async def bind_app_user_id(
    db: AsyncSession, http: httpx.AsyncClient, redis: Redis, user: User, candidate: str
) -> bool:
    """Bind `candidate` to `user` if RevenueCat says it is active right now.

    RevenueCat is called before any row is locked. The client path only seeds
    state: once a webhook has written to the row for this id, sync leaves
    is_premium and the expiry alone. If another user holds the id, the
    binding moves. That is what a restore on a new account looks like, and
    it is logged as a warning.

    Returns:
        True when RevenueCat confirmed the entitlement and the id is bound
        to `user`. False when it is not active or another request bound the
        id first.

    Raises:
        AppError: 503 when the row lock cannot be taken in time.
    """
    entitlement = await check_entitlement(http, redis, candidate, fresh=True)
    log_extra = {"user_id": str(user.id), "app_user_id": candidate}
    if not entitlement.active:
        logger.info(
            "Not binding RevenueCat id",
            extra={**log_extra, "status": entitlement.status.value},
        )
        return False

    try:
        bound = await _bind_locked(db, user, candidate, entitlement.expires_at, log_extra)
    except DBAPIError as exc:
        await db.rollback()
        logger.warning("Bind failed on the database", extra={**log_extra, "error": str(exc)})
        raise AppError(503, "service_unavailable", "Service temporarily unavailable") from exc
    return bound


async def _bind_locked(
    db: AsyncSession,
    user: User,
    candidate: str,
    expires_at: datetime | None,
    log_extra: dict[str, str],
) -> bool:
    await set_lock_timeout(db)
    row = (
        await db.execute(
            select(User)
            .where(User.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    if row.rc_app_user_id == candidate:
        if row.rc_last_event_at is None:
            grant(row, expires_at)
        await db.commit()
        return True

    result = cast(
        "CursorResult[Any]",
        await db.execute(
            update(User)
            .where(User.rc_app_user_id == candidate, User.id != row.id)
            .values(
                rc_app_user_id=None,
                is_premium=False,
                premium_expires_at=None,
                rc_last_event_at=None,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    if result.rowcount:
        logger.warning(
            "RevenueCat id moved from another user",
            extra={**log_extra, "released": result.rowcount},
        )

    try:
        async with db.begin_nested():
            row.rc_app_user_id = candidate
            row.rc_last_event_at = None
            grant(row, expires_at)
    except IntegrityError:
        # Another request bound the id between our update and this write.
        # Let it win and report the row as it is now.
        await db.commit()
        await db.refresh(row)
        logger.warning("Concurrent bind of RevenueCat id, yielding", extra=log_extra)
        return False

    await db.commit()
    logger.info("Bound RevenueCat id", extra=log_extra)
    return True
