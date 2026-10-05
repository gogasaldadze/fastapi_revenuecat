from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.auth import CurrentUser
from app.clients import HttpClient, RedisClient
from app.db import DbSession
from app.models import APP_USER_ID_MAX_LENGTH
from app.revenuecat.premium import bind_app_user_id, normalize_app_user_id, premium_for_user

router = APIRouter(prefix="/subscription", tags=["subscription"])


class SyncRequest(BaseModel):
    app_user_id: str = Field(max_length=APP_USER_ID_MAX_LENGTH)


class SyncResponse(BaseModel):
    is_premium: bool


@router.post("/sync")
async def sync_subscription(
    body: SyncRequest,
    user: CurrentUser,
    db: DbSession,
    redis: RedisClient,
    http: HttpClient,
) -> SyncResponse:
    """Call after a purchase or restore. The id is only bound if RevenueCat
    confirms an active entitlement for it, so a client cannot claim premium."""
    candidate = normalize_app_user_id(body.app_user_id)
    if candidate is not None and await bind_app_user_id(db, http, redis, user, candidate):
        return SyncResponse(is_premium=True)
    return SyncResponse(is_premium=await premium_for_user(http, redis, user))
