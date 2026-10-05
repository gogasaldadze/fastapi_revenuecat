import hmac
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import WebhookAck
from app.core.clients import RedisClient
from app.core.config import settings
from app.core.errors import AppError, ErrorResponse
from app.db.session import DbSession
from app.services.webhook_events import process_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_authorization(authorization: str) -> None:
    """Check the Authorization header against REVENUECAT_WEBHOOK_SECRET.

    Accepts "Bearer <secret>" or the bare secret, whichever you typed in the
    RevenueCat dashboard.

    Raises:
        AppError: 401 for a wrong secret, 503 when no secret is configured
            outside debug mode.
    """
    secret = settings.revenuecat_webhook_secret
    if not secret:
        if settings.debug:
            logger.warning("REVENUECAT_WEBHOOK_SECRET not set, skipping auth in debug")
            return
        logger.error("REVENUECAT_WEBHOOK_SECRET not set, rejecting webhook")
        raise AppError(503, "webhook_not_configured", "Webhook verification not configured")

    token = authorization.strip()
    if token[:7].lower() == "bearer ":
        token = token[7:].strip()
    if not hmac.compare_digest(token.encode(), secret.encode()):
        raise AppError(401, "invalid_webhook_authorization", "Invalid webhook authorization")


@router.post(
    "/revenuecat",
    summary="Receive RevenueCat webhook events",
    response_model_exclude_none=True,
    responses={
        401: {"model": ErrorResponse, "description": "Wrong Authorization header"},
        503: {"model": ErrorResponse, "description": "Webhook secret not configured"},
    },
)
async def revenuecat_webhook(
    request: Request,
    db: DbSession,
    redis: RedisClient,
    authorization: Annotated[str, Header()] = "",
) -> WebhookAck:
    """Auth problems get 401 or 503. Anything else is logged and answered with 200.

    RevenueCat retries a failed delivery five times over about two and a half
    hours, and a broken handler would fail the same way every time.
    """
    verify_authorization(authorization)

    try:
        payload = json.loads(await request.body())
        event = payload.get("event") if isinstance(payload, dict) else None
        if not isinstance(event, dict):
            logger.warning("RevenueCat webhook without an event object, ignored")
            return WebhookAck(status="ignored")
        bucket = await process_event(db, redis, event)
    except Exception:
        logger.exception("RevenueCat webhook handler failed")
        await _safe_rollback(db)
        return WebhookAck(status="error_logged")

    return WebhookAck(status="ok", bucket=bucket.value)


async def _safe_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        logger.exception("Rollback after webhook failure failed")
