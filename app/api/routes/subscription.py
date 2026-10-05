from fastapi import APIRouter

from app.api.schemas import PremiumStatus, SyncRequest
from app.core.clients import HttpClient, RedisClient
from app.core.errors import ErrorResponse
from app.core.security import CurrentUser
from app.db.session import DbSession
from app.services.premium import bind_app_user_id, normalize_app_user_id, premium_for_user

router = APIRouter(prefix="/subscription", tags=["subscription"])


@router.post(
    "/sync",
    summary="Verify a purchase and bind the RevenueCat id",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def sync_subscription(
    body: SyncRequest,
    user: CurrentUser,
    db: DbSession,
    redis: RedisClient,
    http: HttpClient,
) -> PremiumStatus:
    """Call after a purchase or restore.

    The id is bound to the current user only if RevenueCat confirms an active
    entitlement for it, so a client cannot claim premium by sending an id.
    """
    candidate = normalize_app_user_id(body.app_user_id)
    if candidate is not None and await bind_app_user_id(db, http, redis, user, candidate):
        return PremiumStatus(is_premium=True)
    return PremiumStatus(is_premium=await premium_for_user(http, redis, user))
