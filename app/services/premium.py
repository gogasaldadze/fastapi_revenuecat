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
from typing import Any, cast

import httpx
from redis.asyncio import Redis
from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import as_utc, utcnow
from app.db.models import APP_USER_ID_MAX_LENGTH, User
from app.services.entitlements import EntitlementStatus, check_entitlement

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

    If another user holds the id, the binding moves. That is what a restore on
    a new account looks like, and it is logged as a warning.

    Returns:
        True when the user is premium through `candidate` afterwards.
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
        result = cast(
            "CursorResult[Any]",
            await db.execute(
                update(User)
                .where(User.rc_app_user_id == candidate, User.id != user.id)
                .values(rc_app_user_id=None, is_premium=False, premium_expires_at=None)
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount:
            logger.warning(
                "RevenueCat id moved from another user",
                extra={**log_extra, "released": result.rowcount},
            )
        user.rc_app_user_id = candidate
        user.is_premium = True
        user.premium_expires_at = entitlement.expires_at
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning("Concurrent bind of RevenueCat id, skipped", extra=log_extra)
        return False

    logger.info("Bound RevenueCat id", extra=log_extra)
    return True
