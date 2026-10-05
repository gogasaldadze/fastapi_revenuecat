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

import httpx
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import APP_USER_ID_MAX_LENGTH, User
from app.revenuecat.entitlements import EntitlementStatus, check_entitlement
from app.timeutil import as_utc, utcnow

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
    if not user.is_premium:
        return False
    expires = user.premium_expires_at
    return expires is None or as_utc(expires) > utcnow()


async def premium_for_user(http: httpx.AsyncClient, redis: Redis, user: User) -> bool:
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
    """
    entitlement = await check_entitlement(http, redis, candidate, fresh=True)
    if not entitlement.active:
        logger.info("Not binding %s to user %s: %s", candidate, user.id, entitlement.status)
        return False

    try:
        released = await db.execute(
            update(User)
            .where(User.rc_app_user_id == candidate, User.id != user.id)
            .values(rc_app_user_id=None, is_premium=False, premium_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        if released.rowcount:
            logger.warning(
                "RevenueCat id %s moved to user %s from %d other user(s)",
                candidate,
                user.id,
                released.rowcount,
            )
        user.rc_app_user_id = candidate
        user.is_premium = True
        user.premium_expires_at = entitlement.expires_at
        await db.commit()
    except IntegrityError:
        await db.rollback()
        logger.warning("Concurrent bind of %s for user %s, skipped", candidate, user.id)
        return False

    logger.info("Bound RevenueCat id %s to user %s", candidate, user.id)
    return True
