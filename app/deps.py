"""Dependencies for gating routes on premium status and daily quota."""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException

from app.auth import CurrentUser
from app.clients import HttpClient, RedisClient
from app.config import settings
from app.models import User
from app.revenuecat.premium import premium_for_user
from app.services.quota import QuotaBackendError, consume_units

logger = logging.getLogger(__name__)

PREMIUM_REQUIRED = "Premium subscription required"
DAILY_LIMIT_REACHED = "Daily limit reached"
SERVICE_UNAVAILABLE = "Service temporarily unavailable"


async def get_is_premium(user: CurrentUser, redis: RedisClient, http: HttpClient) -> bool:
    # FastAPI caches this per request, so stacking require_premium and
    # charge() on one route checks RevenueCat once.
    return await premium_for_user(http, redis, user)


IsPremium = Annotated[bool, Depends(get_is_premium)]


async def require_premium(user: CurrentUser, is_premium: IsPremium) -> User:
    if not is_premium:
        raise HTTPException(status_code=403, detail=PREMIUM_REQUIRED)
    return user


PremiumUser = Annotated[User, Depends(require_premium)]


def charge(feature: str) -> Callable[..., Awaitable[None]]:
    """Charge the feature weight against the user's daily units.

    Features listed in FAIL_OPEN_FEATURES are let through when Redis is down.
    Everything else gets 503, so an outage cannot run up costs.
    """
    if feature not in settings.feature_weights:
        raise ValueError(f"No weight configured for feature {feature!r}")

    async def dependency(user: CurrentUser, is_premium: IsPremium, redis: RedisClient) -> None:
        weight = settings.feature_weights[feature]
        limit = settings.premium_daily_units if is_premium else settings.free_daily_units
        try:
            allowed = await consume_units(redis, str(user.id), weight, limit)
        except QuotaBackendError as exc:
            if feature in settings.fail_open_features:
                logger.warning("Quota backend down, allowing %s for %s: %s", feature, user.id, exc)
                return
            logger.error("Quota backend down, rejecting %s for %s: %s", feature, user.id, exc)
            raise HTTPException(status_code=503, detail=SERVICE_UNAVAILABLE) from exc
        if not allowed:
            raise HTTPException(status_code=429, detail=DAILY_LIMIT_REACHED)

    return dependency
