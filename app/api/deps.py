"""Dependencies for gating routes on premium status and daily quota."""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends

from app.core.clients import HttpClient, RedisClient
from app.core.config import settings
from app.core.errors import AppError
from app.core.security import CurrentUser
from app.db.models import User
from app.services.premium import premium_for_user
from app.services.quota import QuotaBackendError, consume_units

logger = logging.getLogger(__name__)


async def get_is_premium(user: CurrentUser, redis: RedisClient, http: HttpClient) -> bool:
    """Server side premium status for the current user."""
    # FastAPI caches this per request, so stacking require_premium and
    # charge() on one route checks RevenueCat once.
    return await premium_for_user(http, redis, user)


IsPremium = Annotated[bool, Depends(get_is_premium)]


async def require_premium(user: CurrentUser, is_premium: IsPremium) -> User:
    """Return the user if premium.

    Raises:
        AppError: 403 premium_required.
    """
    if not is_premium:
        raise AppError(403, "premium_required", "Premium subscription required")
    return user


PremiumUser = Annotated[User, Depends(require_premium)]


def charge(feature: str) -> Callable[..., Awaitable[None]]:
    """Build a dependency that charges `feature` against the daily units.

    Features in FAIL_OPEN_FEATURES are let through when Redis is down.
    Everything else gets 503, so an outage cannot run up costs.

    Args:
        feature: A key of FEATURE_WEIGHTS.

    Raises:
        ValueError: At import time, when the feature has no weight.
    """
    if feature not in settings.feature_weights:
        raise ValueError(f"No weight configured for feature {feature!r}")

    async def dependency(user: CurrentUser, is_premium: IsPremium, redis: RedisClient) -> None:
        weight = settings.feature_weights[feature]
        limit = settings.premium_daily_units if is_premium else settings.free_daily_units
        log_extra = {"user_id": str(user.id), "feature": feature}
        try:
            allowed = await consume_units(redis, str(user.id), weight, limit)
        except QuotaBackendError as exc:
            if feature in settings.fail_open_features:
                logger.warning(
                    "Quota backend down, allowing", extra={**log_extra, "error": str(exc)}
                )
                return
            logger.error("Quota backend down, rejecting", extra={**log_extra, "error": str(exc)})
            raise AppError(503, "service_unavailable", "Service temporarily unavailable") from exc
        if not allowed:
            raise AppError(429, "daily_limit_reached", "Daily limit reached")

    return dependency
