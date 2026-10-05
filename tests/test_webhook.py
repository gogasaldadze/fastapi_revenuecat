import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.db.models import SubscriptionEvent, User
from app.services import webhook_events
from app.services.entitlement_cache import cache_key
from tests.conftest import WEBHOOK_SECRET, make_user, reload

URL = "/webhooks/revenuecat"
EXPIRES_MS = 1893456000000  # 2030-01-01


def payload(event_type: str, app_user_id: str = "rc_1", **extra) -> dict:
    return {
        "event": {
            "id": "evt_1",
            "type": event_type,
            "app_user_id": app_user_id,
            "original_app_user_id": app_user_id,
            "aliases": [app_user_id],
            "product_id": "example_monthly",
            "environment": "PRODUCTION",
            "store": "APP_STORE",
            "expiration_at_ms": EXPIRES_MS,
            **extra,
        }
    }


def bearer(token: str = WEBHOOK_SECRET) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def event_rows(session_maker) -> list[SubscriptionEvent]:
    async with session_maker() as s:
        return list((await s.execute(select(SubscriptionEvent))).scalars())


async def user_count(session_maker) -> int:
    async with session_maker() as s:
        return (await s.execute(select(func.count(User.id)))).scalar_one()


# Auth


async def test_wrong_secret_is_401(client):
    resp = await client.post(URL, json=payload("RENEWAL"), headers=bearer("nope"))
    assert resp.status_code == 401


async def test_missing_header_is_401(client):
    resp = await client.post(URL, json=payload("RENEWAL"))
    assert resp.status_code == 401


async def test_bearer_secret_accepted(client):
    resp = await client.post(URL, json=payload("RENEWAL"), headers=bearer())
    assert resp.status_code == 200


async def test_bare_secret_accepted(client):
    resp = await client.post(
        URL, json=payload("RENEWAL"), headers={"Authorization": WEBHOOK_SECRET}
    )
    assert resp.status_code == 200


async def test_lowercase_bearer_accepted(client):
    headers = {"Authorization": f"bearer {WEBHOOK_SECRET}"}
    resp = await client.post(URL, json=payload("RENEWAL"), headers=headers)
    assert resp.status_code == 200


async def test_unset_secret_in_production_is_503(client, monkeypatch):
    monkeypatch.setattr(settings, "revenuecat_webhook_secret", "")
    resp = await client.post(URL, json=payload("RENEWAL"), headers=bearer())
    assert resp.status_code == 503


async def test_unset_secret_in_debug_is_processed(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "revenuecat_webhook_secret", "")
    monkeypatch.setattr(settings, "debug", True)
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    resp = await client.post(URL, json=payload("INITIAL_PURCHASE"))
    assert resp.status_code == 200
    assert (await reload(session_maker, user)).is_premium is True


# Buckets


@pytest.mark.parametrize(
    "event_type", ["INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"]
)
async def test_sync_events_grant_premium(client, session_maker, event_type):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    resp = await client.post(URL, json=payload(event_type), headers=bearer())
    assert resp.json() == {"status": "ok", "bucket": "sync"}
    row = await reload(session_maker, user)
    assert row.is_premium is True
    assert row.premium_expires_at.replace(tzinfo=UTC) == datetime(2030, 1, 1, tzinfo=UTC)


async def test_sync_without_expiration_is_lifetime(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    body = payload("INITIAL_PURCHASE", expiration_at_ms=None)
    await client.post(URL, json=body, headers=bearer())
    row = await reload(session_maker, user)
    assert row.is_premium is True
    assert row.premium_expires_at is None


@pytest.mark.parametrize("event_type", ["EXPIRATION", "SUBSCRIPTION_PAUSED"])
async def test_expire_events_revoke_premium(client, session_maker, event_type):
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    resp = await client.post(URL, json=payload(event_type), headers=bearer())
    assert resp.json()["bucket"] == "expire"
    assert (await reload(session_maker, user)).is_premium is False


@pytest.mark.parametrize("event_type", ["CANCELLATION", "BILLING_ISSUE"])
async def test_info_only_events_keep_access(client, session_maker, event_type):
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    resp = await client.post(URL, json=payload(event_type), headers=bearer())
    assert resp.json()["bucket"] == "info_only"
    assert (await reload(session_maker, user)).is_premium is True
    assert [e.event_type for e in await event_rows(session_maker)] == [event_type]


async def test_unknown_event_is_recorded_only(client, session_maker, caplog):
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    with caplog.at_level(logging.INFO):
        resp = await client.post(URL, json=payload("TRANSFER"), headers=bearer())
    assert resp.json()["bucket"] == "unknown"
    assert (await reload(session_maker, user)).is_premium is True
    assert [e.bucket for e in await event_rows(session_maker)] == ["unknown"]
    assert "Unhandled RevenueCat event type 'TRANSFER'" in caplog.text


async def test_lowercase_event_type_is_classified(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await client.post(URL, json=payload("renewal"), headers=bearer())
    assert (await reload(session_maker, user)).is_premium is True


# Sandbox


async def test_sandbox_ignored_in_production(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    body = payload("INITIAL_PURCHASE", environment="SANDBOX")
    resp = await client.post(URL, json=body, headers=bearer())
    assert resp.json()["bucket"] == "sandbox_ignored"
    assert (await reload(session_maker, user)).is_premium is False
    assert await event_rows(session_maker) == []


async def test_sandbox_processed_when_allowed(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "allow_sandbox_subscriptions", True)
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    body = payload("INITIAL_PURCHASE", environment="SANDBOX")
    await client.post(URL, json=body, headers=bearer())
    assert (await reload(session_maker, user)).is_premium is True


# Matching


async def test_matches_by_alias(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="$RCAnonymousID:abc")
    body = payload("RENEWAL", app_user_id="real_id")
    body["event"]["aliases"] = ["real_id", "$RCAnonymousID:abc"]
    await client.post(URL, json=body, headers=bearer())
    assert (await reload(session_maker, user)).is_premium is True


async def test_matches_by_original_app_user_id(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="original")
    body = payload("RENEWAL", app_user_id="new_id", original_app_user_id="original", aliases=[])
    await client.post(URL, json=body, headers=bearer())
    assert (await reload(session_maker, user)).is_premium is True


async def test_unbound_anonymous_id_is_skipped(client, session_maker, caplog):
    other = await make_user(session_maker, rc_app_user_id="someone_else")
    with caplog.at_level(logging.INFO):
        body = payload("INITIAL_PURCHASE", app_user_id="$RCAnonymousID:xyz")
        resp = await client.post(URL, json=body, headers=bearer())
    assert resp.status_code == 200
    assert (await reload(session_maker, other)).is_premium is False
    assert "No user bound to anonymous id" in caplog.text


async def test_webhook_never_creates_bindings(client, session_maker):
    user = await make_user(session_maker)
    await client.post(
        URL, json=payload("INITIAL_PURCHASE", app_user_id="free_id"), headers=bearer()
    )
    row = await reload(session_maker, user)
    assert row.rc_app_user_id is None
    assert row.is_premium is False
    assert await user_count(session_maker) == 1


async def test_bogus_ids_are_ignored(client, session_maker):
    body = payload("RENEWAL", app_user_id="", original_app_user_id=None, aliases="not a list")
    resp = await client.post(URL, json=body, headers=bearer())
    assert resp.status_code == 200
    assert (await event_rows(session_maker))[0].app_user_id is None


# Failure handling


async def test_handler_error_still_returns_200(client, session_maker, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("database fell over")

    monkeypatch.setattr("app.api.routes.webhooks.process_event", boom)
    resp = await client.post(URL, json=payload("RENEWAL"), headers=bearer())
    assert resp.status_code == 200
    assert resp.json() == {"status": "error_logged"}


async def test_invalid_json_returns_200(client):
    headers = {**bearer(), "Content-Type": "application/json"}
    resp = await client.post(URL, content=b"{not json", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "error_logged"}


async def test_missing_event_object_is_ignored(client):
    resp = await client.post(URL, json={"api_version": "1.0"}, headers=bearer())
    assert resp.json() == {"status": "ignored"}


async def test_bad_expiration_does_not_break_processing(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    body = payload("RENEWAL", expiration_at_ms="soon")
    await client.post(URL, json=body, headers=bearer())
    assert (await reload(session_maker, user)).is_premium is True


# Side effects


async def test_event_is_stored(client, session_maker):
    await client.post(URL, json=payload("RENEWAL"), headers=bearer())
    (row,) = await event_rows(session_maker)
    assert (row.event_id, row.event_type, row.bucket, row.app_user_id) == (
        "evt_1",
        "RENEWAL",
        "sync",
        "rc_1",
    )
    assert row.expiration_at is not None


async def test_event_storage_can_be_disabled(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "store_webhook_events", False)
    await client.post(URL, json=payload("RENEWAL"), headers=bearer())
    assert await event_rows(session_maker) == []


async def test_entitlement_cache_is_busted(client, redis):
    await redis.set(cache_key("rc_1"), '{"status": "inactive", "expires_at": null}')
    await redis.set(cache_key("alias_2"), '{"status": "inactive", "expires_at": null}')
    body = payload("RENEWAL")
    body["event"]["aliases"] = ["rc_1", "alias_2"]
    await client.post(URL, json=body, headers=bearer())
    assert await redis.get(cache_key("rc_1")) is None
    assert await redis.get(cache_key("alias_2")) is None


def test_classify_buckets():
    assert webhook_events.classify("INITIAL_PURCHASE") is webhook_events.Bucket.SYNC
    assert webhook_events.classify("EXPIRATION") is webhook_events.Bucket.EXPIRE
    assert webhook_events.classify("BILLING_ISSUE") is webhook_events.Bucket.INFO_ONLY
    assert webhook_events.classify("") is webhook_events.Bucket.UNKNOWN


def test_event_user_ids_dedupes_in_order():
    event = {"app_user_id": "a", "original_app_user_id": "b", "aliases": ["b", "a", "c", 7]}
    assert webhook_events.event_user_ids(event) == ("a", "b", "c")


async def test_rollback_failure_is_swallowed(client, session_maker, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async def boom(*args, **kwargs):
        raise RuntimeError("handler failed")

    async def rollback_fails(self):
        raise RuntimeError("rollback failed")

    monkeypatch.setattr("app.api.routes.webhooks.process_event", boom)
    monkeypatch.setattr(AsyncSession, "rollback", rollback_fails)
    resp = await client.post(URL, json=payload("RENEWAL"), headers=bearer())
    assert resp.json() == {"status": "error_logged"}
