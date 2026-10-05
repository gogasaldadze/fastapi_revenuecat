"""Request and response models for the HTTP API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import APP_USER_ID_MAX_LENGTH


class SyncRequest(BaseModel):
    """Body for POST /subscription/sync."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"app_user_id": "6f1c2a7e-3b0d-4c55-9a8e-2f4b1d7c9e10"}]}
    )

    app_user_id: str = Field(
        max_length=APP_USER_ID_MAX_LENGTH,
        description="Purchases.shared.appUserID from the RevenueCat SDK.",
    )


class PremiumStatus(BaseModel):
    """Premium state for the current user."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"is_premium": True}]})

    is_premium: bool


class MeResponse(PremiumStatus):
    """The current user."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"id": "6f1c2a7e-3b0d-4c55-9a8e-2f4b1d7c9e10", "is_premium": False}]
        }
    )

    id: str


class TokenResponse(BaseModel):
    """A bearer token for a freshly created user."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "bearer",
                    "user_id": "6f1c2a7e-3b0d-4c55-9a8e-2f4b1d7c9e10",
                }
            ]
        }
    )

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user_id: str


class WebhookAck(BaseModel):
    """Webhook reply. Always sent with 200 once auth has passed."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok", "bucket": "sync"}]})

    status: Literal["ok", "ignored", "error_logged"]
    bucket: str | None = None


class HealthResponse(BaseModel):
    """Liveness reply."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"status": "ok"}]})

    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    """Readiness reply with one entry per dependency."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"status": "ok", "checks": {"database": "ok", "redis": "ok"}}]
        }
    )

    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "error"]]


class ExampleResponse(BaseModel):
    """Reply from the example routes."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"ok": True}]})

    ok: bool = True
