import hmac
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import RedisClient
from app.config import settings
from app.db import DbSession
from app.revenuecat.webhook_events import process_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_authorization(authorization: str) -> None:
    """Accept "Bearer <secret>" or the bare secret, as set in the RevenueCat dashboard."""
    secret = settings.revenuecat_webhook_secret
    if not secret:
        if settings.debug:
            logger.warning("REVENUECAT_WEBHOOK_SECRET not set, skipping auth in debug")
            return
        logger.error("REVENUECAT_WEBHOOK_SECRET not set, rejecting webhook")
        raise HTTPException(status_code=503, detail="Webhook verification not configured")

    token = authorization.strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()
    if not hmac.compare_digest(token.encode(), secret.encode()):
        raise HTTPException(status_code=401, detail="Invalid webhook authorization")


@router.post("/revenuecat")
async def revenuecat_webhook(
    request: Request,
    db: DbSession,
    redis: RedisClient,
    authorization: Annotated[str, Header()] = "",
) -> dict:
    """Auth problems get 401 or 503. Anything else is logged and answered with 200,
    because RevenueCat retries failed deliveries for about a day and a broken
    handler would fail the same way every time."""
    verify_authorization(authorization)

    try:
        payload = json.loads(await request.body())
        event = payload.get("event") if isinstance(payload, dict) else None
        if not isinstance(event, dict):
            logger.warning("RevenueCat webhook without an event object, ignored")
            return {"status": "ignored"}
        bucket = await process_event(db, redis, event)
    except Exception:
        logger.exception("RevenueCat webhook handler failed")
        await _safe_rollback(db)
        return {"status": "error_logged"}

    return {"status": "ok", "bucket": bucket.value}


async def _safe_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        logger.exception("Rollback after webhook failure failed")
