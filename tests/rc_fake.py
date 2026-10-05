"""Builders for RevenueCat v1 subscriber payloads and a fake API for respx."""

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import httpx

RC_SUBSCRIBERS_URL = "https://api.revenuecat.com/v1/subscribers/"
ENTITLEMENT = "premium"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def future(days: float = 30) -> str:
    return iso(datetime.now(UTC) + timedelta(days=days))


def past(days: float = 1) -> str:
    return iso(datetime.now(UTC) - timedelta(days=days))


def subscriber(
    expires: str | None = None,
    *,
    sandbox: bool = False,
    product: str = "example_monthly",
    entitlement: str = ENTITLEMENT,
    grace: str | None = None,
    has_entitlement: bool = True,
) -> dict:
    entitlements = {}
    if has_entitlement:
        entitlements[entitlement] = {
            "expires_date": expires,
            "grace_period_expires_date": grace,
            "product_identifier": product,
            "purchase_date": past(10),
        }
    return {
        "subscriber": {
            "entitlements": entitlements,
            "subscriptions": {product: {"is_sandbox": sandbox, "expires_date": expires}},
            "non_subscriptions": {},
        }
    }


def active() -> dict:
    return subscriber(future())


def expired() -> dict:
    return subscriber(past())


class FakeRevenueCat:
    """Maps app_user_id to a canned response and records every call."""

    def __init__(self) -> None:
        self.responses: dict[str, tuple[int, dict | None]] = {}
        self.calls: list[str] = []
        self.network_error = False

    def set(self, app_user_id: str, status: int = 200, body: dict | None = None) -> None:
        self.responses[app_user_id] = (status, body)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        app_user_id = unquote(request.url.raw_path.decode().rsplit("/", 1)[-1])
        self.calls.append(app_user_id)
        if self.network_error:
            raise httpx.ConnectError("connection refused", request=request)
        status, body = self.responses.get(app_user_id, (404, {"code": 7259}))
        return httpx.Response(status, json=body if body is not None else {})


def with_ids(body: dict, *, original: str | None = None, aliases: list[str] | None = None) -> dict:
    """Add RevenueCat identity fields to a subscriber payload."""
    sub = body["subscriber"]
    if original is not None:
        sub["original_app_user_id"] = original
    if aliases is not None:
        sub["aliases"] = aliases
    return body
