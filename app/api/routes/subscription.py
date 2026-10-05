from fastapi import APIRouter

from app.api.schemas import PremiumStatus, SyncRequest
from app.core.clients import HttpClient, RedisClient
from app.core.config import settings
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

    With REVENUECAT_REQUIRE_USER_ID_MATCH on (the default), the server checks
    the signed in user's own id. A different id is accepted only when
    RevenueCat lists the user's id as one of its aliases. With it off, any
    id RevenueCat confirms as active is bound, moving it from another user
    if needed. That mode exists only for migrating apps on anonymous ids.
    """
    claimed = normalize_app_user_id(body.app_user_id)
    if settings.revenuecat_require_user_id_match:
        own_id = str(user.id)
        # An id other than the user's own counts only if RevenueCat aliases
        # it to the user, for example a purchase made before logIn.
        candidates = [c for c in (claimed, own_id) if c is not None]
        for candidate in dict.fromkeys(candidates):
            if await bind_app_user_id(db, http, redis, user, candidate, owner_id=own_id):
                return PremiumStatus(is_premium=True)
    elif claimed is not None and await bind_app_user_id(db, http, redis, user, claimed):
        return PremiumStatus(is_premium=True)
    return PremiumStatus(is_premium=await premium_for_user(http, redis, user))
